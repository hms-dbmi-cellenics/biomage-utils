
import sys
import json 

import boto3
import click
from biomage.experiment.utils import (add_env_user_to_experiment,
                                      create_gem2s_hash,
                                      get_experiment_project_id)
from botocore.exceptions import ClientError
from click.utils import echo

from ..utils.constants import PRODUCTION, STAGING


def remap_sample_references(samples, sandbox_id):
    """
    Edit entries in samples dictionary to opoint to the right prefixed resources
    """

    remapped_samples = {"M" : {}}

    for sample_id in samples['M']:

        prefixed_sample_name = f"{sandbox_id}-{sample_id}"
        prefixed_project_uuid = f"{sandbox_id}-{samples['M'][sample_id]['M']['projectUuid']['S']}"

        remapped_samples['M'][prefixed_sample_name] = {
                "M": {
                    **samples['M'][sample_id]['M'],
                    "files": remap_file_references(
                        samples['M'][sample_id]["M"]["files"],
                        sandbox_id
                    ),
                    "uuid": {"S" : prefixed_sample_name},
                    "projectUuid": {
                        "S" : prefixed_project_uuid
                    }
                }
        }

    return remapped_samples


def remap_file_references(files, sandbox_id):
    """
    Edit entries in files dictionary to opoint to the right prefixed resources
    """

    valid_filenames = [file for file in files["M"] if file != "lastModified"]
    
    for file in valid_filenames:
        files['M'][file]['M']['path']['S'] = f"{sandbox_id}-{files['M'][file]['M']['path']['S']}"

    return files


def modify_records(item, target_table, config, **extra):
    """
    Return modified records.
    This function should return spreadable dictionary
    """

    if target_table == config["staging-experiments-table"]:

        item['projectId']['S'] = f"{extra['sandbox_id']}-{item['projectId']['S']}"
        item = add_env_user_to_experiment(cfg=item)

        return item

    if target_table == config["staging-samples-table"]:
        return {
            "projectUuid": {"S" : f"{extra['sandbox_id']}-{item['projectUuid']['S']}"},
            "samples": remap_sample_references(item['samples'], extra['sandbox_id'])
        }

    if target_table == config["staging-projects-table"]:

        new_experiments_list = []
        for experiment_id in item["projects"]["M"]["experiments"]["L"]:
            new_experiments_list.append({"S" : f"{extra['sandbox_id']}-{experiment_id['S']}"})

        item["projects"]["M"]["experiments"]["L"] = new_experiments_list

        item["projects"]["M"]["uuid"]["S"] = f"{extra['sandbox_id']}-{item['projectUuid']['S']}"

        new_samples_list = []
        for samples_id in item["projects"]["M"]["samples"]["L"]:
            new_samples_list.append({"S" : f"{extra['sandbox_id']}-{samples_id['S']}"})

        item["projects"]["M"]["samples"]["L"] = new_samples_list

        return item

    return {}


def prefix_cell_set_samples_key(sandbox_id, cell_sets):
    """
    Prefix sandbox_id to sample keys in cell-sets object
    """
    for root_idx in range(len(cell_sets["cellSets"])):

        if cell_sets["cellSets"][root_idx].get("key") != "sample":
            continue

        for cell_set_idx in range(len(cell_sets["cellSets"][root_idx]["children"])):
            new_key = f"{sandbox_id}-{cell_sets['cellSets'][root_idx]['children'][cell_set_idx]['key']}"
            cell_sets["cellSets"][root_idx]["children"][cell_set_idx]["key"] = new_key

    return cell_sets


def definitely_equal(target, source):
    """
    Returns if 2 objects are equal. Only positive return values are reliable. Two
    objects might be equal and return false due to a number of reasons like:
    * We can't reliably use etags for object comparison
    * If there's any exception trying to get the target bucket, we'll just return false.

    The method is only useful to avoid copying again objects that are definitely
    equal.
    """
    same_etag = False

    try:
        s3 = boto3.client("s3")
        s3.head_object(
            Bucket=target["Bucket"], Key=target["Key"], IfMatch=source["ETag"]
        )
        same_etag = True
    except ClientError:
        # if there's any exception assume the comparison failed a return false
        #  (which can be a false negative or a true negative)
        pass

    return same_etag


def copy_s3_files(sandbox_id, prefix, source_bucket, target_bucket):
    """
    Copy s3 files in a bucket under a prefix
    """
    s3 = boto3.client("s3")
    exp_files = s3.list_objects_v2(Bucket=source_bucket, Prefix=prefix)

    if 'Contents' not in exp_files:
        raise Exception(f"Failed to do an experiment copy: bucket {source_bucket} doesn't contain {prefix} as prefix.")
    
    for obj in exp_files.get("Contents"):

        experiment_id = obj["Key"].split("/")[0]
        target_key = obj["Key"].replace(experiment_id, f"{sandbox_id}-{experiment_id}")

        # biomage-originals- uses projectId/sampleId/file schema,
        # so sampleId has to be prefixed too
        if 'biomage-originals-' in target_bucket:
            sample_id = obj["Key"].split("/")[1]
            target_key = target_key.replace(sample_id, f"{sandbox_id}-{sample_id}")

        source = {"Bucket": source_bucket, "Key": obj["Key"]}

        target = {
            "Bucket": target_bucket,
            "Key": target_key,
        }

        if not definitely_equal(target, obj):
            click.echo(
                f"Copying from {source['Bucket']}/{source['Key']} to "
                f"{target['Bucket']}/{target['Key']}"
            )
            try:
                if 'cell-sets-' in target_bucket:
                    content_stream = s3.get_object(
                        Bucket=source["Bucket"], 
                        Key=source["Key"]
                    )

                    cell_sets_json = json.loads(content_stream['Body'].read().decode('UTF-8'))
                    cell_sets_json = prefix_cell_set_samples_key(sandbox_id, cell_sets_json)

                    s3.put_object(
                        Body=json.dumps(cell_sets_json),
                        Bucket=target["Bucket"],
                        Key=target["Key"]
                    )
                    continue

                s3.copy_object(
                    CopySource=source,
                    Bucket=target["Bucket"],
                    Key=target["Key"],
                )
            except Exception as e:
                click.echo(
                    f"failed to copy object {source['Bucket']}/{source['Key']} \
                    with exception: \n {e}"
                )


def copy_dynamodb_records(
    sandbox_id, staging_experiments, source_table, target_table, config
):
    """
    Copy dynamodBD records for an experiment id
    """

    if 'projects-' in source_table:
        copy_project_record(
            sandbox_id, staging_experiments, source_table, target_table, config
        )
        return

    dynamodb = boto3.client("dynamodb")
    for experiment_id in staging_experiments:
        items = dynamodb.query(
            TableName=source_table,
            KeyConditionExpression="experimentId = :experiment_id",
            ExpressionAttributeValues={":experiment_id": {"S": experiment_id}},
        ).get("Items")

        items_to_insert = {
            target_table: [
                {
                    "PutRequest": {
                        "Item": {
                            **item,
                            **modify_records(
                                item,
                                target_table,
                                config,
                                sandbox_id=sandbox_id
                            ),
                            "experimentId": {
                                "S": f"{sandbox_id}-{item['experimentId']['S']}",
                            },
                        }
                    }
                }
                for item in items
            ]
        }

        try:
            dynamodb.batch_write_item(RequestItems=items_to_insert)
        except Exception as e:
            click.echo(f"Failed inserting records: {e}")


def copy_project_record(
    sandbox_id, staging_experiments, source_table, target_table, config
):

    dynamodb = boto3.client("dynamodb")
    for experiment_id in staging_experiments:

        project_id = get_experiment_project_id(
            experiment_id,
            config['production-experiments-table']
        )

        item = dynamodb.get_item(
            TableName=source_table,
            Key={"projectUuid": {'S' : project_id}}
        ).get("Item")

        dynamodb.put_item(
            TableName=target_table,
            Item={
                **modify_records(
                    item,
                    target_table,
                    config,
                    sandbox_id=sandbox_id
                ),
                "projectUuid": {
                    "S": f"{sandbox_id}-{project_id}"
                },
            },
        )


def insert_new_gem2s_hash(sandbox_id, experiments, source_experiments_table):

    source_projects_table = source_experiments_table.replace('experiments', 'projects')
    source_samples_table = source_experiments_table.replace('experiments', 'samples')

    client = boto3.client('dynamodb')

    for experiment_id in experiments:

        prefixed_experiment_id = f"{sandbox_id}-{experiment_id}"

        experiment = client.get_item(
            TableName=source_experiments_table,
            Key={'experimentId' : {'S' : prefixed_experiment_id}}
        ).get("Item")

        project = client.get_item(
            TableName=source_projects_table,
            Key={"projectUuid": experiment['projectId']},
        ).get("Item")['projects']

        samples = client.get_item(
            TableName=source_samples_table,
            Key={"experimentId": {"S" : prefixed_experiment_id}},
        ).get("Item")['samples']

        gem2s_hash = create_gem2s_hash(experiment, project, samples)

        client.update_item(
            TableName=source_experiments_table,
            Key={'experimentId' : {'S' : prefixed_experiment_id}},
            UpdateExpression='SET meta.gem2s.paramsHash = :hash_string',
            ExpressionAttributeValues={':hash_string': {'S': gem2s_hash}}
        )

        click.echo(
            f"Inserted new GEM2S params for experiment {experiment_id} "
            + f"in {source_experiments_table}"
        )


def copy_experiments_to(
    experiments, sandbox_id, config, origin=PRODUCTION, destination=STAGING
):
    """
    Copy the list of experiment IDs in experiments from the origin env into
    destination env.
    """
    click.echo()
    click.echo("Copying items for new experiments...")

    buckets = config["source-buckets"]
    # Copy files
    for source_bucket in buckets:
        target_bucket = source_bucket.replace(origin, destination)

        for experiment_id in experiments:
            if "biomage-originals-" in target_bucket:
                project_id = get_experiment_project_id(
                    experiment_id,
                    config['production-experiments-table']
                )
                copy_s3_files(sandbox_id, project_id, source_bucket, target_bucket)
                continue

            copy_s3_files(sandbox_id, experiment_id, source_bucket, target_bucket)

    click.echo(click.style("S3 files successfully copied.", fg="green", bold=True))
    click.echo()

    # Copy DynamoDB entries
    click.echo("Copying DynamoDB records for new experiments...")
    for source_table in config["source-tables"]:
        target_table = source_table.replace(origin, destination)

        click.echo(f"Copying records from {source_table} to table {target_table}...")
        copy_dynamodb_records(
            sandbox_id, experiments, source_table, target_table, config
        )

    # Create new GEM2S paramsHash to prevent rerunning of pipeline
    click.echo("Creating new GEM2S params to prevent rerunning pipeline...")
    insert_new_gem2s_hash(sandbox_id, experiments, config['staging-experiments-table'])

    click.echo(
        click.style("DynamoDB records successfully copied.", fg="green", bold=True)
    )
