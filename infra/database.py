"""
DijkFood — Infrastructure: RDS PostgreSQL + DynamoDB Tables
"""
import logging
import time

import boto3

logger = logging.getLogger(__name__)

IAM_ROLE = "LabRole"


def get_lab_role_arn(iam_client):
    """Obtém o ARN da LabRole."""
    try:
        role = iam_client.get_role(RoleName=IAM_ROLE)
        return role["Role"]["Arn"]
    except Exception as e:
        logger.error(f"Erro ao obter LabRole: {e}")
        raise


def create_rds(rds_client, vpc_config):
    """Cria instância RDS PostgreSQL com Multi-AZ."""
    logger.info("=== Criando RDS PostgreSQL ===")

    # Criar ou atualizar DB Subnet Group
    try:
        rds_client.create_db_subnet_group(
            DBSubnetGroupName="dijkfood-db-subnet-group",
            DBSubnetGroupDescription="Subnets para RDS DijkFood",
            SubnetIds=vpc_config["public_subnets"]
        )
        logger.info("DB Subnet Group criado")
    except rds_client.exceptions.DBSubnetGroupAlreadyExistsFault:
        logger.info("DB Subnet Group já existe. Atualizando subnets...")
        rds_client.modify_db_subnet_group(
            DBSubnetGroupName="dijkfood-db-subnet-group",
            DBSubnetGroupDescription="Subnets para RDS DijkFood",
            SubnetIds=vpc_config["public_subnets"]
        )
        logger.info("DB Subnet Group atualizado com sucesso")

    # Criar instância RDS
    rds_client.create_db_instance(
        DBInstanceIdentifier="dijkfood-db",
        DBInstanceClass="db.t3.medium",
        Engine="postgres",
        EngineVersion="15",
        MasterUsername="dijkfood",
        MasterUserPassword="DijkFood2026!Secure",
        DBName="dijkfood",
        AllocatedStorage=100,
        StorageType="gp3",
        MultiAZ=True,
        VpcSecurityGroupIds=[vpc_config["sg_rds_id"]],
        DBSubnetGroupName="dijkfood-db-subnet-group",
        PubliclyAccessible=True,
        BackupRetentionPeriod=7,
        StorageEncrypted=True,
        Tags=[{"Key": "Name", "Value": "dijkfood-db"}]
    )
    logger.info("Instância RDS criada, aguardando disponibilidade...")

    # Aguardar RDS disponível
    waiter = rds_client.get_waiter("db_instance_available")
    waiter.wait(
        DBInstanceIdentifier="dijkfood-db",
        WaiterConfig={"Delay": 30, "MaxAttempts": 60}
    )

    # Obter endpoint
    db_info = rds_client.describe_db_instances(DBInstanceIdentifier="dijkfood-db")
    endpoint = db_info["DBInstances"][0]["Endpoint"]
    db_host = endpoint["Address"]
    db_port = endpoint["Port"]

    logger.info(f"RDS disponível: {db_host}:{db_port}")

    return {
        "db_host": db_host,
        "db_port": db_port,
        "db_name": "dijkfood",
        "db_user": "dijkfood",
        "db_pass": "DijkFood2026!Secure",
    }


def init_rds_schema(db_config):
    """Executa o schema SQL no RDS PostgreSQL."""
    logger.info("=== Inicializando schema do PostgreSQL ===")
    import subprocess
    import os

    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql", "schema.sql")

    # Usar psql via subprocess
    env = os.environ.copy()
    env["PGPASSWORD"] = db_config["db_pass"]

    try:
        result = subprocess.run(
            [
                "psql",
                "-h", db_config["db_host"],
                "-p", str(db_config["db_port"]),
                "-U", db_config["db_user"],
                "-d", db_config["db_name"],
                "-f", schema_path,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            logger.info("Schema PostgreSQL aplicado com sucesso")
        else:
            logger.error(f"Erro ao aplicar schema: {result.stderr}")
    except FileNotFoundError:
        logger.warning("psql não encontrado, tentando via asyncpg...")
        _init_schema_asyncpg(db_config, schema_path)


def _init_schema_asyncpg(db_config, schema_path):
    """Fallback: aplica schema usando asyncpg."""
    import asyncio
    import asyncpg

    async def _apply():
        conn = await asyncpg.connect(
            host=db_config["db_host"],
            port=db_config["db_port"],
            user=db_config["db_user"],
            password=db_config["db_pass"],
            database=db_config["db_name"]
        )
        with open(schema_path, "r") as f:
            schema_sql = f.read()
        # Executar cada statement separadamente
        statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
        for stmt in statements:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"Erro ao executar statement: {e}")
        await conn.close()
        logger.info("Schema aplicado via asyncpg")

    asyncio.run(_apply())


def create_dynamodb_tables(dynamodb_client):
    """Cria tabelas DynamoDB necessárias."""
    logger.info("=== Criando tabelas DynamoDB ===")

    tables = [
        {
            "TableName": "courier_positions",
            "KeySchema": [{"AttributeName": "courier_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "courier_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": "order_status_cache",
            "KeySchema": [{"AttributeName": "order_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "order_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": "anomalies",
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": "historical_averages",
            "KeySchema": [
                {"AttributeName": "metric", "KeyType": "HASH"},
                {"AttributeName": "dimension", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "metric", "AttributeType": "S"},
                {"AttributeName": "dimension", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    created_tables = []
    for table_def in tables:
        table_name = table_def["TableName"]
        try:
            dynamodb_client.create_table(**table_def)
            logger.info(f"Tabela DynamoDB '{table_name}' criada")
            created_tables.append(table_name)
        except dynamodb_client.exceptions.ResourceInUseException:
            logger.info(f"Tabela '{table_name}' já existe")
            created_tables.append(table_name)
        except Exception as e:
            logger.error(f"Erro ao criar tabela '{table_name}': {e}")

    # Aguardar tabelas ficarem ativas
    for table_name in created_tables:
        try:
            waiter = dynamodb_client.get_waiter("table_exists")
            waiter.wait(TableName=table_name)

            # Habilitar TTL nas tabelas que precisam
            if table_name in ["courier_positions", "anomalies"]:
                try:
                    dynamodb_client.update_time_to_live(
                        TableName=table_name,
                        TimeToLiveSpecification={
                            "Enabled": True,
                            "AttributeName": "ttl"
                        }
                    )
                    logger.info(f"TTL habilitado em '{table_name}'")
                except Exception as e:
                    logger.warning(f"Erro ao habilitar TTL em '{table_name}': {e}")

            # Habilitar DynamoDB Streams em courier_positions
            if table_name == "courier_positions":
                try:
                    dynamodb_client.update_table(
                        TableName=table_name,
                        StreamSpecification={
                            "StreamEnabled": True,
                            "StreamViewType": "NEW_AND_OLD_IMAGES"
                        }
                    )
                    logger.info(f"DynamoDB Streams habilitado em '{table_name}'")
                except Exception as e:
                    logger.warning(f"Erro ao habilitar Streams em '{table_name}': {e}")

        except Exception as e:
            logger.warning(f"Erro ao aguardar tabela '{table_name}': {e}")

    logger.info("Tabelas DynamoDB criadas")
    return created_tables


def destroy_rds(rds_client):
    """Destrói instância RDS e subnet group."""
    logger.info("=== Destruindo RDS ===")

    try:
        rds_client.delete_db_instance(
            DBInstanceIdentifier="dijkfood-db",
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True
        )
        logger.info("Instância RDS em processo de deleção...")

        # Aguardar deleção
        waiter = rds_client.get_waiter("db_instance_deleted")
        waiter.wait(
            DBInstanceIdentifier="dijkfood-db",
            WaiterConfig={"Delay": 30, "MaxAttempts": 60}
        )
        logger.info("Instância RDS deletada")
    except Exception as e:
        logger.warning(f"Erro ao deletar RDS: {e}")

    try:
        rds_client.delete_db_subnet_group(DBSubnetGroupName="dijkfood-db-subnet-group")
        logger.info("DB Subnet Group deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar DB Subnet Group: {e}")


def destroy_dynamodb_tables(dynamodb_client):
    """Destrói tabelas DynamoDB."""
    logger.info("=== Destruindo tabelas DynamoDB ===")

    for table_name in ["courier_positions", "order_status_cache", "anomalies", "historical_averages"]:
        try:
            dynamodb_client.delete_table(TableName=table_name)
            logger.info(f"Tabela '{table_name}' deletada")
        except Exception as e:
            logger.warning(f"Erro ao deletar tabela '{table_name}': {e}")
