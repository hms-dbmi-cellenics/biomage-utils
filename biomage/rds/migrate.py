import os
import subprocess
from pathlib import Path

import click

from ..utils.AuroraClient import AuroraClient
from ..utils.constants import DEFAULT_AWS_ACCOUNT_ID, DEVELOPMENT, STAGING

# Assuming that biomage-utils and iac root folders are located in the same folder
MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IAC_PATH = os.path.join(MODULE_PATH, "../../../iac")
IAC_PATH = os.getenv("BIOMAGE_IAC_PATH", DEFAULT_IAC_PATH)


def _migrate(command, iac_path, migration_env):
    proc = subprocess.Popen(
        ["node_modules/.bin/knex", command, "--cwd", iac_path],
        cwd=iac_path,
        env=migration_env,
    )
    proc.wait()


@click.command()
@click.option(
    "-i",
    "--input_env",
    required=True,
    default=DEVELOPMENT,
    show_default=True,
    help="Path to the IAC folder",
)
@click.option(
    "-s",
    "--sandbox_id",
    required=False,
    default=None,
    show_default=True,
    help="Sandbox id to migrate to. Required if migrating to staging",
)
@click.option(
    "--iac_path",
    required=False,
    default=IAC_PATH,
    show_default=True,
    help="Path to the IAC folder",
)
@click.option(
    "-c",
    "--command",
    required=False,
    default="migrate:latest",
    show_default=True,
    help="Knex command to execute",
)
def migrate(iac_path, sandbox_id, input_env, command):
    """
    Runs knex migration command in local or staged env. Runs migrate:latest if no command is provided

    Examples.:\n
        biomage rds migrate -i staging -s <sandbox_id>\n
        biomage rds migrate -i staging -s <sandbox_id> -c migrate:rollback
    """

    REGION = "eu-west-1"
    AWS_PROFILE = "default"
    AWS_ACCOUNT_ID = "000000000000"
    USER = "dev_role"
    LOCAL_PORT = 5431

    if input_env == STAGING:
        AWS_ACCOUNT_ID = DEFAULT_AWS_ACCOUNT_ID
        LOCAL_PORT = 5432

    iac_path = os.path.join(iac_path, "migrations/sql-migrations/")

    migration_env = {
        **os.environ,
        "NODE_ENV": input_env,
        "SANDBOX_ID": str(sandbox_id),
        "AWS_ACCOUNT_ID": AWS_ACCOUNT_ID,
        "AWS_REGION": REGION,
    }

    if input_env == DEVELOPMENT:
        _migrate(command, iac_path, migration_env)
    else:
        if not sandbox_id:
            raise Exception(
                "Migrating to staging but sandbox id is not set. Set sandbox id by setting the value of the the -s option."
            )

        with AuroraClient(sandbox_id, USER, REGION, input_env, AWS_PROFILE, LOCAL_PORT):
            _migrate(command, iac_path, migration_env)
