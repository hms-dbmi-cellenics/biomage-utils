import json
import time
from functools import reduce

import boto3
import cfn_flip
import click
import requests
from botocore.config import Config
from github import Github

from ..utils.encrypt import encrypt

config = Config(
    region_name="eu-west-1",
)

DEFAULT_ORG = "hms-dbmi-cellenics"


def recursive_get(d, *keys):
    return reduce(lambda c, k: c.get(k, {}), keys, d)


def filter_iam_repos(repo):
    if repo.archived:
        return False

    # get files in root
    contents = repo.get_contents("")

    for content in contents:
        # search for tags.y(a)ml file
        if content.path != ".ci.yml" and content.path != ".ci.yaml":
            continue

        # open contents
        tags = cfn_flip.to_json(content.decoded_content)

        tags = json.loads(tags)

        if recursive_get(tags, "ci-policies"):
            return repo.name, recursive_get(tags, "ci-policies")

        return False

    return False


# CF template names can't contain underscores or dashes, remove them and capitalize
# the string
def format_name_for_cf(repo_name):
    return repo_name.replace("_", " ").replace("-", " ").title().replace(" ", "")


def get_ci_names(org):
    stack_name = "biomage-ci-users"
    path_prefix = "ci-users"
    name_prefix = "ci-user"

    if org.login != DEFAULT_ORG:
        org_postfix = org.login
        stack_name = f"biomage-ci-users-{org_postfix}"
        path_prefix = f"{path_prefix}/{org_postfix}"
        name_prefix = f"{name_prefix}-{org_postfix}"

    return stack_name, path_prefix, name_prefix


def create_new_iam_users(org, policies):

    stack_name, path_prefix, name_prefix = get_ci_names(org)

    users = {}

    for repo, policies in policies.items():
        users[f"{format_name_for_cf(repo)}CIUser"] = {
            "Path": f"/{path_prefix}/{repo}/",
            "UserName": f"{name_prefix}-{repo}",
            "Policies": policies,
        }

    stack_cfg = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Set up GitHub CI users with appropriate rights "
        "[managed by github.com/biomage-utils, command `biomage rotate-ci`]",
        "Resources": {
            name: {"Type": "AWS::IAM::User", "Properties": properties}
            for name, properties in users.items()
        },
    }

    stack_cfg = cfn_flip.to_yaml(json.dumps(stack_cfg))
    cf = boto3.client("cloudformation", config=config)

    kwargs = {
        "StackName": stack_name,
        "TemplateBody": stack_cfg,
        "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    }

    try:
        stack = cf.create_stack(**kwargs)
    except Exception as e:
        if "AlreadyExistsException" in str(e):
            try:
                stack = cf.update_stack(**kwargs)
            except Exception as e:
                if "No updates are to be performed" in str(e):
                    click.echo("All users are up to date.")
                    return
                else:
                    raise e
        else:
            raise e

    click.echo(
        "Now creating CloudFormation stack. Waiting for completion...",
        nl=False,
    )

    while True:
        time.sleep(10)
        response = cf.describe_stacks(StackName=stack["StackId"])

        status = response["Stacks"][0]["StackStatus"]

        if "FAILED" in status or "ROLLBACK" in status or "DELETE" in status:
            click.echo()
            click.echo(
                click.style(
                    f"✖️ Stack creation failed with error {status}. "
                    "Check the AWS Console for more details.",
                    fg="red",
                    bold=True,
                )
            )
            exit(1)
        elif "COMPLETE" in status:
            click.echo()
            click.echo(f"Stack successfully created with status {status}.")
            break
        else:
            click.echo(".", nl=False)

    click.echo("Created new users.")


def create_new_access_keys(iam, org, roles):
    click.echo("Now creating new access keys for users...")
    keys = {}

    _, _, name_prefix = get_ci_names(org)

    for repo in roles:
        key = iam.create_access_key(UserName=f"{name_prefix}-{repo}")
        keys[repo] = (
            key["AccessKey"]["AccessKeyId"],
            key["AccessKey"]["SecretAccessKey"],
        )

    return keys


def update_github_secrets(keys, token, org):
    click.echo("Now updating all repositories with new keys...")

    s = requests.Session()
    s.headers = {"Authorization": f"token {token}", "User-Agent": "Requests"}
    url_base = f"https://api.github.com/repos/{org.login}"

    results = {}

    for repo_name, (access_key_id, secret_access_key) in keys.items():
        ci_keys = s.get(f"{url_base}/{repo_name}/actions/secrets/public-key")

        if ci_keys.status_code != requests.codes.ok:
            results[repo_name] = ci_keys.status_code
            continue

        ci_keys = ci_keys.json()

        access_key_id = encrypt(ci_keys["key"], access_key_id)
        secret_access_key = encrypt(ci_keys["key"], secret_access_key)
        encrypted_token = encrypt(ci_keys["key"], token)

        r = s.put(
            f"{url_base}/{repo_name}/actions/secrets/AWS_ACCESS_KEY_ID",
            json={"encrypted_value": access_key_id, "key_id": ci_keys["key_id"]},
        )

        r = s.put(
            f"{url_base}/{repo_name}/actions/secrets/AWS_SECRET_ACCESS_KEY",
            json={"encrypted_value": secret_access_key, "key_id": ci_keys["key_id"]},
        )

        r = s.put(
            f"{url_base}/{repo_name}/actions/secrets/API_TOKEN_GITHUB",
            json={"encrypted_value": encrypted_token, "key_id": ci_keys["key_id"]},
        )

        results[repo_name] = r.status_code

    return results


def rollback_if_necessary(iam, keys, org, result_codes):
    click.echo("Results for each repository:")

    success = True

    _, _, name_prefix = get_ci_names(org)

    click.echo(
        "{0:<15}{1:<25}{2:<15}".format("REPOSITORY", "UPDATE STATUS (HTTP)", "STATUS")
    )
    for repo, code in result_codes.items():

        status = None
        username = f"{name_prefix}-{repo}"
        generated_key_id, _ = keys[repo]

        if not 200 <= code <= 299:
            iam.delete_access_key(UserName=username, AccessKeyId=generated_key_id)
            status = "Key rolled back"
            success = False
        else:
            user_keys = iam.list_access_keys(UserName=username)
            user_keys = user_keys["AccessKeyMetadata"]

            keys_deleted = 0

            for key in user_keys:
                if key["AccessKeyId"] == generated_key_id:
                    continue

                iam.delete_access_key(UserName=username, AccessKeyId=key["AccessKeyId"])
                keys_deleted += 1

            status = f"Removed {keys_deleted} old keys"

        click.echo(
            click.style(
                f"{repo:<15}{code:<25}{status:<15}",
                fg="green" if 200 <= code <= 299 else "red",
            )
        )

    return success


@click.command()
@click.option(
    "--token",
    "-t",
    envvar="GITHUB_API_TOKEN",
    required=True,
    show_default=True,
    help="A GitHub Personal Access Token with the required permissions.",
)
@click.option(
    "--org",
    "-o",
    envvar="GITHUB_BIOMAGE_ORG",
    default=DEFAULT_ORG,
    show_default=True,
    help="The GitHub organization to perform the operation in.",
)
def rotate_ci(token, org):
    """
    Rotates and updates repository access credentials.
    """

    click.echo("Logging into GitHub and getting all repositories...")

    g = Github(token)
    org = g.get_organization(org)
    repos = org.get_repos()

    click.echo(
        f"Found {repos.totalCount} "
        f"repositories in organization {org.name} ({org.login}), "
        "finding ones with required CI privileges..."
    )

    policies = [ret for ret in (filter_iam_repos(repo) for repo in repos) if ret]
    click.echo(
        f"Found {len(policies)} repositories marked as requiring CI IAM policies."
    )
    policies = dict(policies)

    create_new_iam_users(org, policies)

    iam = boto3.client("iam", config=config)
    keys = create_new_access_keys(iam, org, policies)

    result_codes = update_github_secrets(keys, token, org)

    success = rollback_if_necessary(iam, keys, org, result_codes)

    if success:
        click.echo(click.style("✔️ All done!", fg="green", bold=True))
        exit(0)
    else:
        click.echo(
            click.style(
                "✖️ There have been errors. Check the logs and try again.",
                fg="red",
                bold=True,
            )
        )
        exit(1)
