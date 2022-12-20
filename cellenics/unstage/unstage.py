import base64
import json

import boto3
import click
from github import Github
from inquirer import Confirm, prompt
from inquirer.themes import GreenPassion

from ..utils.staging import check_if_sandbox_exists


@click.command()
@click.argument("sandbox_id", nargs=1)
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
    envvar="GITHUB_CELLENICS_ORG",
    default="hms-dbmi-cellenics",
    show_default=True,
    help="The GitHub organization to perform the operation in.",
)
def unstage(token, org, sandbox_id):
    """
    Removes a custom staging environment.
    """

    if check_if_sandbox_exists(org, sandbox_id):
        # get (secret) access keys
        session = boto3.Session()
        credentials = session.get_credentials()
        credentials = credentials.get_frozen_credentials()

        credentials = {
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "github_api_token": token,
        }

        # encrypt (secret) access keys
        kms = boto3.client("kms")
        secrets = kms.encrypt(
            KeyId="alias/iac-secret-key", Plaintext=json.dumps(credentials).encode()
        )
        secrets = base64.b64encode(secrets["CiphertextBlob"]).decode()

        questions = [
            Confirm(
                "delete",
                default=False,
                message="Are you sure you want to remove the sandbox "
                f"with ID `{sandbox_id}`. This cannot be undone.",
            )
        ]
        click.echo()
        answers = prompt(questions, theme=GreenPassion())
        if not answers["delete"]:
            exit(1)

        g = Github(token)
        o = g.get_organization(org)
        r = o.get_repo("iac")

        wf = None
        for workflow in r.get_workflows():
            if workflow.name == "Remove a staging environment":
                wf = str(workflow.id)

        wf = r.get_workflow(wf)

        wf.create_dispatch(
            ref="master",
            inputs={"sandbox-id": sandbox_id, "secrets": secrets},
        )

        click.echo()
        click.echo(
            click.style(
                "✔️ Removal submitted. You can check your progress at "
                f"https://github.com/{org}/iac/actions",
                fg="green",
                bold=True,
            )
        )

    else:
        click.echo()
        click.echo(
            click.style(
                f"Staging sandbox with ID `{sandbox_id}` could not be found.",
                fg="yellow",
                bold=True,
            )
        )
