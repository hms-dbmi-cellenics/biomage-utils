import sys
import signal
import boto3
import click

import threading

from ..utils.constants import STAGING

from subprocess import PIPE, run

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
    "-p",
    "--port",
    required=False,
    default=5432,
    show_default=True,
    help="Port of the db.",
)

@click.option(
    "-u",
    "--user",
    required=False,
    default="dev_role",
    show_default=True,
    help="User to connect as (role is the same as user).",
)

@click.option(
    "-r",
    "--region",
    required=False,
    default="eu-west-1",
    show_default=True,
    help="Role to connect as (role is the same as user).",
)

def login(input_env, port, user, region):
    """
    Logs into a database using psql and IAM if necessary.\n

    E.g.:
    biomage rds login
    """
    password = None

    internal_port = port

    if input_env == "development":
        password = "password"
    else:
        internal_port = 5432
        print("Only local port 5432 works connecting to staging and prod for now, so setting it to 5432")

        rds_client = boto3.client("rds")

        remote_endpoint = get_rds_writer_endpoint(input_env, rds_client)

        print(f"Generating temporary token for {input_env}")
        password = rds_client.generate_db_auth_token(remote_endpoint, internal_port, user, region)

    print("Token generated")

    run(f"PGPASSWORD=\"{password}\" psql --host=localhost --port={internal_port} --username={user} --dbname=aurora_db", shell=True)

def get_rds_writer_endpoint(input_env, rds_client):
    response = rds_client.describe_db_cluster_endpoints(
        DBClusterIdentifier=f"aurora-cluster-{input_env}",
        Filters=[
            {
                'Name': 'db-cluster-endpoint-type',
                'Values': ['writer']
            },
        ],
    )

    return response["DBClusterEndpoints"][0]["Endpoint"]