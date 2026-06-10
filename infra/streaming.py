"""
DijkFood — Infrastructure: Kinesis Data Streams + Firehose + Lambda Event Source Mapping
"""
import io
import json
import logging
import os
import time
import zipfile

import boto3

logger = logging.getLogger(__name__)

IAM_ROLE = "LabRole"


def get_lab_role_arn(iam_client):
    """Obtém o ARN da LabRole."""
    role = iam_client.get_role(RoleName=IAM_ROLE)
    return role["Role"]["Arn"]


def create_kinesis_stream(kinesis_client):
    """Cria o Kinesis Data Stream."""
    logger.info("=== Criando Kinesis Data Stream ===")

    try:
        kinesis_client.create_stream(
            StreamName="dijkfood-events",
            ShardCount=4
        )
        logger.info("Kinesis stream 'dijkfood-events' criado")
    except kinesis_client.exceptions.ResourceInUseException:
        logger.info("Stream 'dijkfood-events' já existe")

    # Aguardar stream ativo
    for _ in range(60):
        desc = kinesis_client.describe_stream(StreamName="dijkfood-events")
        status = desc["StreamDescription"]["StreamStatus"]
        if status == "ACTIVE":
            stream_arn = desc["StreamDescription"]["StreamARN"]
            logger.info(f"Kinesis stream ativo: {stream_arn}")
            return stream_arn
        time.sleep(5)

    raise TimeoutError("Kinesis stream não ficou ativo")


def create_firehose(firehose_client, stream_arn, s3_bucket, lab_role_arn, glue_database="dijkfood_analytics", glue_table="events"):
    """
    Cria Kinesis Firehose com conversão para Parquet via AWS Glue.
    Source: Kinesis Data Stream
    Destination: S3 (formato Parquet)
    """
    logger.info("=== Criando Kinesis Firehose ===")

    try:
        firehose_client.create_delivery_stream(
            DeliveryStreamName="dijkfood-firehose",
            DeliveryStreamType="KinesisStreamAsSource",
            KinesisStreamSourceConfiguration={
                "KinesisStreamARN": stream_arn,
                "RoleARN": lab_role_arn,
            },
            ExtendedS3DestinationConfiguration={
                "RoleARN": lab_role_arn,
                "BucketARN": f"arn:aws:s3:::{s3_bucket}",
                "Prefix": "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/",
                "ErrorOutputPrefix": "errors/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/!{firehose:error-output-type}/",
                "BufferingHints": {
                    "SizeInMBs": 5,
                    "IntervalInSeconds": 60,
                },
                "CompressionFormat": "UNCOMPRESSED",  # Parquet já tem compressão interna
                "DataFormatConversionConfiguration": {
                    "Enabled": True,
                    "SchemaConfiguration": {
                        "RoleARN": lab_role_arn,
                        "DatabaseName": glue_database,
                        "TableName": glue_table,
                        "Region": "us-east-1",
                    },
                    "InputFormatConfiguration": {
                        "Deserializer": {
                            "OpenXJsonSerDe": {}
                        }
                    },
                    "OutputFormatConfiguration": {
                        "Serializer": {
                            "ParquetSerDe": {
                                "Compression": "SNAPPY"
                            }
                        }
                    },
                },
            },
        )
        logger.info("Firehose 'dijkfood-firehose' criado com conversão Parquet via Glue")
    except firehose_client.exceptions.ResourceInUseException:
        logger.info("Firehose 'dijkfood-firehose' já existe")
    except Exception as e:
        logger.error(f"Erro ao criar Firehose: {e}")
        # Fallback sem conversão Parquet
        logger.info("Tentando criar Firehose sem conversão Parquet...")
        firehose_client.create_delivery_stream(
            DeliveryStreamName="dijkfood-firehose",
            DeliveryStreamType="KinesisStreamAsSource",
            KinesisStreamSourceConfiguration={
                "KinesisStreamARN": stream_arn,
                "RoleARN": lab_role_arn,
            },
            ExtendedS3DestinationConfiguration={
                "RoleARN": lab_role_arn,
                "BucketARN": f"arn:aws:s3:::{s3_bucket}",
                "Prefix": "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/",
                "ErrorOutputPrefix": "errors/",
                "BufferingHints": {
                    "SizeInMBs": 5,
                    "IntervalInSeconds": 60,
                },
                "CompressionFormat": "GZIP",
            },
        )
        logger.info("Firehose criado com GZIP (fallback)")


def create_lambda_anomaly_detector(lambda_client, stream_arn, lab_role_arn):
    """Cria a Lambda de detecção de anomalias com trigger do Kinesis."""
    logger.info("=== Criando Lambda Anomaly Detector ===")

    # Criar ZIP com o código
    lambda_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lambda", "anomaly_detector")
    handler_path = os.path.join(lambda_dir, "handler.py")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, "handler.py")
    zip_buffer.seek(0)

    # Criar função Lambda
    try:
        lambda_client.create_function(
            FunctionName="dijkfood-anomaly-detector",
            Runtime="python3.12",
            Role=lab_role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": zip_buffer.read()},
            Description="Detecta anomalias operacionais em eventos do Kinesis",
            Timeout=60,
            MemorySize=256,
            Environment={
                "Variables": {
                    "ANOMALY_TABLE": "anomalies",
                    "HISTORICAL_TABLE": "historical_averages",
                }
            },
            Tags={"Project": "DijkFood"},
        )
        logger.info("Lambda 'dijkfood-anomaly-detector' criada")
    except lambda_client.exceptions.ResourceConflictException:
        logger.info("Lambda já existe, atualizando código...")
        zip_buffer.seek(0)
        lambda_client.update_function_code(
            FunctionName="dijkfood-anomaly-detector",
            ZipFile=zip_buffer.read()
        )

    # Aguardar Lambda ativa
    time.sleep(5)

    # Criar Event Source Mapping (Kinesis → Lambda)
    try:
        lambda_client.create_event_source_mapping(
            EventSourceArn=stream_arn,
            FunctionName="dijkfood-anomaly-detector",
            StartingPosition="LATEST",
            BatchSize=100,
            ParallelizationFactor=2,
            Enabled=True,
        )
        logger.info("Event Source Mapping criado: Kinesis → Lambda")
    except Exception as e:
        if "already exists" in str(e).lower() or "ResourceConflictException" in str(type(e).__name__):
            logger.info("Event Source Mapping já existe")
        else:
            logger.error(f"Erro ao criar Event Source Mapping: {e}")


def create_lambda_firehose_transform(lambda_client, lab_role_arn):
    """Cria a Lambda de transformação do Firehose."""
    logger.info("=== Criando Lambda Firehose Transform ===")

    lambda_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lambda", "firehose_transform")
    handler_path = os.path.join(lambda_dir, "handler.py")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, "handler.py")
    zip_buffer.seek(0)

    try:
        lambda_client.create_function(
            FunctionName="dijkfood-firehose-transform",
            Runtime="python3.12",
            Role=lab_role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": zip_buffer.read()},
            Description="Transforma e enriquece eventos antes de gravar no S3",
            Timeout=60,
            MemorySize=128,
            Tags={"Project": "DijkFood"},
        )
        logger.info("Lambda 'dijkfood-firehose-transform' criada")
    except lambda_client.exceptions.ResourceConflictException:
        logger.info("Lambda firehose-transform já existe")


def destroy_streaming(kinesis_client, firehose_client, lambda_client):
    """Destrói recursos de streaming."""
    logger.info("=== Destruindo recursos de streaming ===")

    # Deletar Event Source Mappings da Lambda
    try:
        mappings = lambda_client.list_event_source_mappings(
            FunctionName="dijkfood-anomaly-detector"
        )
        for mapping in mappings.get("EventSourceMappings", []):
            lambda_client.delete_event_source_mapping(UUID=mapping["UUID"])
            logger.info(f"Event Source Mapping {mapping['UUID']} deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar event source mappings: {e}")

    # Deletar Lambdas
    for fn_name in ["dijkfood-anomaly-detector", "dijkfood-firehose-transform"]:
        try:
            lambda_client.delete_function(FunctionName=fn_name)
            logger.info(f"Lambda '{fn_name}' deletada")
        except Exception as e:
            logger.warning(f"Erro ao deletar Lambda '{fn_name}': {e}")

    # Deletar Firehose
    try:
        firehose_client.delete_delivery_stream(
            DeliveryStreamName="dijkfood-firehose",
            AllowForceDelete=True
        )
        logger.info("Firehose 'dijkfood-firehose' deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar Firehose: {e}")

    # Deletar Kinesis Stream
    try:
        kinesis_client.delete_stream(StreamName="dijkfood-events", EnforceConsumerDeletion=True)
        logger.info("Kinesis stream 'dijkfood-events' deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar Kinesis stream: {e}")
