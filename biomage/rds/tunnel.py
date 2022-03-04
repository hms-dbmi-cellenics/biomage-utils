import pathlib
from subprocess import run

import click

from ..utils.constants import STAGING


@click.command()
@click.option(
    "-i",
    "--input_env",
    required=False,
    default=STAGING,
    show_default=True,
    help="Input environment of the RDS server.",
)
@click.option(
    "-r",
    "--region",
    required=False,
    default="eu-west-1",
    show_default=True,
    help="Region the RDS server is in.",
)
# Disabled, it doesn't change anything when there is only one instance
# and might lead to confusion
# @click.option(
#     "-t",
#     "--endpoint_type",
#     required=False,
#     default="reader",
#     show_default=True,
#     help="The type of the rds endpoint you want to connect to,
#           can be either reader or writer",
# )
# Disabled, only 5432 works for now
# @click.option(
#     "-p",
#     "--local_port",
#     required=False,
#     default=5432,
#     show_default=True,
#     help="Local port from which to connect.",
# )
def tunnel(input_env, region, endpoint_type="writer", local_port=5432):
    """
    Sets up an ssh tunneling/port forwarding session
    for the rds server in a given environment.\n

    E.g.:
    biomage rds tunnel -i staging
    """

    file_dir = pathlib.Path(__file__).parent.resolve()
    run(
        f"{file_dir}/tunnel.sh {input_env} {region} {local_port} {endpoint_type}",
        shell=True,
    )
