"""
DijkFood — Infrastructure: S3 Buckets, Glue Crawler, Athena, CloudFront (Camada de Apresentação)
"""
import json
import logging
import time

import boto3

logger = logging.getLogger(__name__)

IAM_ROLE = "LabRole"
REGION = "us-east-1"


def get_lab_role_arn(iam_client):
    role = iam_client.get_role(RoleName=IAM_ROLE)
    return role["Role"]["Arn"]


def create_s3_buckets(s3_client, account_id):
    """Cria buckets S3 necessários."""
    logger.info("=== Criando buckets S3 ===")

    buckets = [
        {
            "name": f"dijkfood-data-lake-{account_id}",
            "purpose": "Data Lake — eventos brutos e processados",
        },
        {
            "name": f"dijkfood-models-{account_id}",
            "purpose": "Modelos ML treinados",
        },
        {
            "name": f"dijkfood-athena-results-{account_id}",
            "purpose": "Resultados de queries Athena",
        },
        {
            "name": f"dijkfood-frontend-{account_id}",
            "purpose": "Frontend estático (static website hosting)",
        },
    ]

    created = {}
    for bucket_def in buckets:
        bucket_name = bucket_def["name"]
        try:
            # us-east-1 não precisa de LocationConstraint
            s3_client.create_bucket(Bucket=bucket_name)

            # Habilitar versionamento
            s3_client.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={"Status": "Enabled"}
            )

            # Encryption padrão (SSE-S3)
            s3_client.put_bucket_encryption(
                Bucket=bucket_name,
                ServerSideEncryptionConfiguration={
                    "Rules": [{
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }]
                }
            )

            logger.info(f"Bucket '{bucket_name}' criado: {bucket_def['purpose']}")
            created[bucket_def["purpose"].split("—")[0].strip().lower().replace(" ", "_")] = bucket_name

        except s3_client.exceptions.BucketAlreadyOwnedByYou:
            logger.info(f"Bucket '{bucket_name}' já existe")
            created[bucket_def["purpose"].split("—")[0].strip().lower().replace(" ", "_")] = bucket_name
        except Exception as e:
            logger.error(f"Erro ao criar bucket '{bucket_name}': {e}")

    # Configurar static website hosting no bucket de frontend
    frontend_bucket = f"dijkfood-frontend-{account_id}"
    try:
        s3_client.put_bucket_website_configuration(
            Bucket=frontend_bucket,
            WebsiteConfiguration={
                "IndexDocument": {"Suffix": "index.html"},
                "ErrorDocument": {"Key": "index.html"},
            }
        )

        # Remover block public access para website hosting
        s3_client.delete_public_access_block(Bucket=frontend_bucket)

        # Política de bucket público para leitura
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{frontend_bucket}/*"
            }]
        }
        s3_client.put_bucket_policy(Bucket=frontend_bucket, Policy=json.dumps(policy))
        logger.info(f"Static website hosting configurado em '{frontend_bucket}'")
    except Exception as e:
        logger.warning(f"Erro ao configurar website hosting: {e}")

    return {
        "data_lake": f"dijkfood-data-lake-{account_id}",
        "models": f"dijkfood-models-{account_id}",
        "athena_results": f"dijkfood-athena-results-{account_id}",
        "frontend": frontend_bucket,
    }


def create_glue_resources(glue_client, s3_buckets, lab_role_arn):
    """Cria database e tabela no AWS Glue para conversão Parquet do Firehose, e Crawler para Athena."""
    logger.info("=== Criando recursos AWS Glue ===")

    # 1. Criar database Glue
    try:
        glue_client.create_database(
            DatabaseInput={
                "Name": "dijkfood_analytics",
                "Description": "Database analítico DijkFood — eventos operacionais"
            }
        )
        logger.info("Glue database 'dijkfood_analytics' criado")
    except glue_client.exceptions.AlreadyExistsException:
        logger.info("Glue database já existe")

    # 2. Criar tabela Glue (necessária para conversão Parquet do Firehose)
    try:
        glue_client.create_table(
            DatabaseName="dijkfood_analytics",
            TableInput={
                "Name": "events",
                "Description": "Eventos operacionais DijkFood",
                "StorageDescriptor": {
                    "Columns": [
                        {"Name": "event_type", "Type": "string"},
                        {"Name": "order_id", "Type": "string"},
                        {"Name": "customer_id", "Type": "string"},
                        {"Name": "restaurant_id", "Type": "string"},
                        {"Name": "courier_id", "Type": "string"},
                        {"Name": "old_status", "Type": "string"},
                        {"Name": "new_status", "Type": "string"},
                        {"Name": "latitude", "Type": "double"},
                        {"Name": "longitude", "Type": "double"},
                        {"Name": "estimated_time", "Type": "double"},
                        {"Name": "user_message", "Type": "string"},
                        {"Name": "bot_response", "Type": "string"},
                        {"Name": "timestamp", "Type": "string"},
                        {"Name": "region", "Type": "string"},
                        {"Name": "hour_of_day", "Type": "int"},
                        {"Name": "day_of_week", "Type": "int"},
                    ],
                    "Location": f"s3://{s3_buckets['data_lake']}/events/",
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    "SerdeInfo": {
                        "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                    },
                },
                "PartitionKeys": [
                    {"Name": "year", "Type": "int"},
                    {"Name": "month", "Type": "int"},
                    {"Name": "day", "Type": "int"},
                    {"Name": "hour", "Type": "int"},
                ],
                "TableType": "EXTERNAL_TABLE",
                "Parameters": {
                    "classification": "parquet",
                    "has_encrypted_data": "false",
                },
            }
        )
        logger.info("Tabela Glue 'events' criada")
    except glue_client.exceptions.AlreadyExistsException:
        logger.info("Tabela Glue 'events' já existe")

    # 3. Criar Glue Crawler para descobrir/atualizar schema automaticamente
    try:
        glue_client.create_crawler(
            Name="dijkfood-crawler",
            Role=lab_role_arn,
            DatabaseName="dijkfood_analytics",
            Targets={
                "S3Targets": [{
                    "Path": f"s3://{s3_buckets['data_lake']}/events/",
                }]
            },
            SchemaChangePolicy={
                "UpdateBehavior": "UPDATE_IN_DATABASE",
                "DeleteBehavior": "LOG",
            },
            RecrawlPolicy={
                "RecrawlBehavior": "CRAWL_EVERYTHING",
            },
            Description="Crawler para descobrir schema dos eventos Parquet no data lake"
        )
        logger.info("Glue Crawler 'dijkfood-crawler' criado")
    except glue_client.exceptions.AlreadyExistsException:
        logger.info("Glue Crawler já existe")

    # 4. Executar Crawler (opcional — pode ser executado depois de ter dados)
    try:
        glue_client.start_crawler(Name="dijkfood-crawler")
        logger.info("Glue Crawler iniciado")
    except Exception as e:
        logger.warning(f"Não foi possível iniciar crawler (normal se não há dados ainda): {e}")

    return {"database": "dijkfood_analytics", "table": "events", "crawler": "dijkfood-crawler"}


def create_cloudfront_distribution(cf_client, frontend_bucket):
    """Cria distribuição CloudFront apontando para o bucket S3 de frontend."""
    logger.info("=== Criando distribuição CloudFront ===")

    try:
        distribution = cf_client.create_distribution(
            DistributionConfig={
                "CallerReference": f"dijkfood-{int(time.time())}",
                "Comment": "DijkFood Frontend CDN",
                "DefaultCacheBehavior": {
                    "TargetOriginId": f"S3-{frontend_bucket}",
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "AllowedMethods": {
                        "Quantity": 2,
                        "Items": ["GET", "HEAD"],
                    },
                    "ForwardedValues": {
                        "QueryString": False,
                        "Cookies": {"Forward": "none"},
                    },
                    "MinTTL": 0,
                    "DefaultTTL": 86400,
                    "MaxTTL": 31536000,
                    "Compress": True,
                },
                "Origins": {
                    "Quantity": 1,
                    "Items": [{
                        "Id": f"S3-{frontend_bucket}",
                        "DomainName": f"{frontend_bucket}.s3-website-{REGION}.amazonaws.com",
                        "CustomOriginConfig": {
                            "HTTPPort": 80,
                            "HTTPSPort": 443,
                            "OriginProtocolPolicy": "http-only",
                        },
                    }],
                },
                "Enabled": True,
                "DefaultRootObject": "index.html",
                "PriceClass": "PriceClass_100",
                "HttpVersion": "http2",
                "CustomErrorResponses": {
                    "Quantity": 1,
                    "Items": [{
                        "ErrorCode": 404,
                        "ResponseCode": "200",
                        "ResponsePagePath": "/index.html",
                        "ErrorCachingMinTTL": 300,
                    }],
                },
            }
        )
        dist_id = distribution["Distribution"]["Id"]
        dist_domain = distribution["Distribution"]["DomainName"]
        logger.info(f"CloudFront distribuição criada: {dist_id} — {dist_domain}")
        return {"distribution_id": dist_id, "domain_name": dist_domain}

    except Exception as e:
        logger.error(f"Erro ao criar CloudFront: {e}")
        return None


def destroy_analytics(s3_client, glue_client, cf_client, s3_buckets, cf_config):
    """Destrói recursos analíticos: S3, Glue, CloudFront."""
    logger.info("=== Destruindo recursos analíticos ===")

    # CloudFront
    if cf_config:
        try:
            # Desabilitar distribuição primeiro
            dist = cf_client.get_distribution(Id=cf_config["distribution_id"])
            etag = dist["ETag"]
            config = dist["Distribution"]["DistributionConfig"]
            config["Enabled"] = False

            cf_client.update_distribution(
                Id=cf_config["distribution_id"],
                IfMatch=etag,
                DistributionConfig=config
            )
            logger.info("CloudFront desabilitado, aguardando...")
            time.sleep(30)

            # Deletar distribuição
            dist = cf_client.get_distribution(Id=cf_config["distribution_id"])
            cf_client.delete_distribution(Id=cf_config["distribution_id"], IfMatch=dist["ETag"])
            logger.info("CloudFront deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar CloudFront: {e}")

    # Glue Crawler
    try:
        glue_client.delete_crawler(Name="dijkfood-crawler")
        logger.info("Glue Crawler deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar Crawler: {e}")

    # Glue Table e Database
    try:
        glue_client.delete_table(DatabaseName="dijkfood_analytics", Name="events")
        logger.info("Glue tabela deletada")
    except Exception as e:
        logger.warning(f"Erro ao deletar tabela Glue: {e}")

    try:
        glue_client.delete_database(Name="dijkfood_analytics")
        logger.info("Glue database deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar database Glue: {e}")

    # S3 — esvaziar e deletar buckets
    for bucket_key, bucket_name in s3_buckets.items():
        try:
            _empty_bucket(s3_client, bucket_name)
            s3_client.delete_bucket(Bucket=bucket_name)
            logger.info(f"Bucket '{bucket_name}' deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar bucket '{bucket_name}': {e}")


def _empty_bucket(s3_client, bucket_name):
    """Esvazia um bucket S3 (incluindo versões)."""
    try:
        # Deletar objetos
        paginator = s3_client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = []
            for version in page.get("Versions", []):
                objects.append({"Key": version["Key"], "VersionId": version["VersionId"]})
            for marker in page.get("DeleteMarkers", []):
                objects.append({"Key": marker["Key"], "VersionId": marker["VersionId"]})

            if objects:
                s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": objects}
                )
        logger.info(f"Bucket '{bucket_name}' esvaziado")
    except Exception as e:
        logger.warning(f"Erro ao esvaziar bucket '{bucket_name}': {e}")
