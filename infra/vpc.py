"""
DijkFood — Infrastructure: VPC, Subnets, IGW, NAT GW, Route Tables, Security Groups
"""
import logging
import time

import boto3

logger = logging.getLogger(__name__)


def create_vpc(ec2_client):
    """Cria a VPC completa: subnets, IGW, NAT GW, route tables, security groups."""
    logger.info("=== Criando VPC e infraestrutura de rede ===")

    # 1. VPC
    vpc = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    ec2_client.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": "dijkfood-vpc"}])
    logger.info(f"VPC criada: {vpc_id}")

    # Aguardar VPC disponível
    waiter = ec2_client.get_waiter("vpc_available")
    waiter.wait(VpcIds=[vpc_id])

    # 2. Subnets públicas (para ALB)
    public_subnet_1 = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone="us-east-1a"
    )
    public_subnet_1_id = public_subnet_1["Subnet"]["SubnetId"]
    ec2_client.create_tags(Resources=[public_subnet_1_id], Tags=[{"Key": "Name", "Value": "dijkfood-public-1a"}])

    public_subnet_2 = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone="us-east-1b"
    )
    public_subnet_2_id = public_subnet_2["Subnet"]["SubnetId"]
    ec2_client.create_tags(Resources=[public_subnet_2_id], Tags=[{"Key": "Name", "Value": "dijkfood-public-1b"}])

    # Habilitar auto-assign IP público nas subnets públicas
    ec2_client.modify_subnet_attribute(SubnetId=public_subnet_1_id, MapPublicIpOnLaunch={"Value": True})
    ec2_client.modify_subnet_attribute(SubnetId=public_subnet_2_id, MapPublicIpOnLaunch={"Value": True})

    # 3. Subnets privadas (para ECS Fargate, RDS)
    private_subnet_1 = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.3.0/24", AvailabilityZone="us-east-1a"
    )
    private_subnet_1_id = private_subnet_1["Subnet"]["SubnetId"]
    ec2_client.create_tags(Resources=[private_subnet_1_id], Tags=[{"Key": "Name", "Value": "dijkfood-private-1a"}])

    private_subnet_2 = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.4.0/24", AvailabilityZone="us-east-1b"
    )
    private_subnet_2_id = private_subnet_2["Subnet"]["SubnetId"]
    ec2_client.create_tags(Resources=[private_subnet_2_id], Tags=[{"Key": "Name", "Value": "dijkfood-private-1b"}])

    logger.info("Subnets criadas")

    # 4. Internet Gateway
    igw = ec2_client.create_internet_gateway()
    igw_id = igw["InternetGateway"]["InternetGatewayId"]
    ec2_client.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    ec2_client.create_tags(Resources=[igw_id], Tags=[{"Key": "Name", "Value": "dijkfood-igw"}])
    logger.info(f"Internet Gateway criado: {igw_id}")

    # 5. Elastic IP para NAT Gateway
    eip = ec2_client.allocate_address(Domain="vpc")
    eip_alloc_id = eip["AllocationId"]

    # 6. NAT Gateway (na subnet pública)
    nat_gw = ec2_client.create_nat_gateway(
        SubnetId=public_subnet_1_id, AllocationId=eip_alloc_id
    )
    nat_gw_id = nat_gw["NatGateway"]["NatGatewayId"]
    ec2_client.create_tags(Resources=[nat_gw_id], Tags=[{"Key": "Name", "Value": "dijkfood-nat"}])
    logger.info(f"NAT Gateway criado: {nat_gw_id}, aguardando ficar disponível...")

    # Aguardar NAT GW disponível
    waiter = ec2_client.get_waiter("nat_gateway_available")
    waiter.wait(NatGatewayIds=[nat_gw_id])
    logger.info("NAT Gateway disponível")

    # 7. Route Tables
    # Pública: rota para IGW
    public_rt = ec2_client.create_route_table(VpcId=vpc_id)
    public_rt_id = public_rt["RouteTable"]["RouteTableId"]
    ec2_client.create_route(
        RouteTableId=public_rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id
    )
    ec2_client.associate_route_table(RouteTableId=public_rt_id, SubnetId=public_subnet_1_id)
    ec2_client.associate_route_table(RouteTableId=public_rt_id, SubnetId=public_subnet_2_id)
    ec2_client.create_tags(Resources=[public_rt_id], Tags=[{"Key": "Name", "Value": "dijkfood-public-rt"}])

    # Privada: rota para NAT GW
    private_rt = ec2_client.create_route_table(VpcId=vpc_id)
    private_rt_id = private_rt["RouteTable"]["RouteTableId"]
    ec2_client.create_route(
        RouteTableId=private_rt_id, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_gw_id
    )
    ec2_client.associate_route_table(RouteTableId=private_rt_id, SubnetId=private_subnet_1_id)
    ec2_client.associate_route_table(RouteTableId=private_rt_id, SubnetId=private_subnet_2_id)
    ec2_client.create_tags(Resources=[private_rt_id], Tags=[{"Key": "Name", "Value": "dijkfood-private-rt"}])
    logger.info("Route tables criadas e associadas")

    # 8. Security Groups
    # SG para ALB
    sg_alb = ec2_client.create_security_group(
        GroupName="dijkfood-sg-alb", Description="SG for ALB", VpcId=vpc_id
    )
    sg_alb_id = sg_alb["GroupId"]
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_alb_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        ]
    )
    ec2_client.create_tags(Resources=[sg_alb_id], Tags=[{"Key": "Name", "Value": "dijkfood-sg-alb"}])

    # SG para ECS
    sg_ecs = ec2_client.create_security_group(
        GroupName="dijkfood-sg-ecs", Description="SG for ECS tasks", VpcId=vpc_id
    )
    sg_ecs_id = sg_ecs["GroupId"]
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_ecs_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8004,
             "UserIdGroupPairs": [{"GroupId": sg_alb_id}]},
        ]
    )
    ec2_client.create_tags(Resources=[sg_ecs_id], Tags=[{"Key": "Name", "Value": "dijkfood-sg-ecs"}])

    # SG para RDS
    sg_rds = ec2_client.create_security_group(
        GroupName="dijkfood-sg-rds", Description="SG for RDS", VpcId=vpc_id
    )
    sg_rds_id = sg_rds["GroupId"]
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_rds_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
             "UserIdGroupPairs": [{"GroupId": sg_ecs_id}]},
            {"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        ]
    )
    ec2_client.create_tags(Resources=[sg_rds_id], Tags=[{"Key": "Name", "Value": "dijkfood-sg-rds"}])
    logger.info("Security Groups criados")

    return {
        "vpc_id": vpc_id,
        "public_subnets": [public_subnet_1_id, public_subnet_2_id],
        "private_subnets": [private_subnet_1_id, private_subnet_2_id],
        "igw_id": igw_id,
        "nat_gw_id": nat_gw_id,
        "eip_alloc_id": eip_alloc_id,
        "sg_alb_id": sg_alb_id,
        "sg_ecs_id": sg_ecs_id,
        "sg_rds_id": sg_rds_id,
        "public_rt_id": public_rt_id,
        "private_rt_id": private_rt_id,
    }


def destroy_vpc(ec2_client, vpc_config):
    """Destrói toda a infraestrutura VPC na ordem inversa."""
    logger.info("=== Destruindo VPC e infraestrutura de rede ===")

    vpc_id = vpc_config["vpc_id"]

    # 1. Deletar NAT Gateway
    try:
        ec2_client.delete_nat_gateway(NatGatewayId=vpc_config["nat_gw_id"])
        logger.info(f"NAT Gateway {vpc_config['nat_gw_id']} deletado (aguardando...)")
        # Aguardar remoção
        for _ in range(60):
            try:
                resp = ec2_client.describe_nat_gateways(NatGatewayIds=[vpc_config["nat_gw_id"]])
                state = resp["NatGateways"][0]["State"]
                if state == "deleted":
                    break
            except Exception:
                break
            time.sleep(5)
    except Exception as e:
        logger.warning(f"Erro ao deletar NAT GW: {e}")

    # 2. Liberar Elastic IP
    try:
        ec2_client.release_address(AllocationId=vpc_config["eip_alloc_id"])
        logger.info("Elastic IP liberado")
    except Exception as e:
        logger.warning(f"Erro ao liberar EIP: {e}")

    # 3. Deletar Security Groups
    for sg_key in ["sg_rds_id", "sg_ecs_id", "sg_alb_id"]:
        try:
            ec2_client.delete_security_group(GroupId=vpc_config[sg_key])
            logger.info(f"SG {vpc_config[sg_key]} deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar SG {sg_key}: {e}")

    # 4. Desassociar e deletar route tables
    for rt_id in [vpc_config.get("public_rt_id"), vpc_config.get("private_rt_id")]:
        if rt_id:
            try:
                # Desassociar
                assocs = ec2_client.describe_route_tables(RouteTableIds=[rt_id])
                for assoc in assocs["RouteTables"][0].get("Associations", []):
                    if not assoc.get("Main", False):
                        ec2_client.disassociate_route_table(
                            AssociationId=assoc["RouteTableAssociationId"]
                        )
                ec2_client.delete_route_table(RouteTableId=rt_id)
                logger.info(f"Route table {rt_id} deletada")
            except Exception as e:
                logger.warning(f"Erro ao deletar RT {rt_id}: {e}")

    # 5. Detach e deletar IGW
    try:
        ec2_client.detach_internet_gateway(InternetGatewayId=vpc_config["igw_id"], VpcId=vpc_id)
        ec2_client.delete_internet_gateway(InternetGatewayId=vpc_config["igw_id"])
        logger.info(f"IGW {vpc_config['igw_id']} deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar IGW: {e}")

    # 6. Deletar subnets
    all_subnets = vpc_config.get("public_subnets", []) + vpc_config.get("private_subnets", [])
    for subnet_id in all_subnets:
        try:
            ec2_client.delete_subnet(SubnetId=subnet_id)
            logger.info(f"Subnet {subnet_id} deletada")
        except Exception as e:
            logger.warning(f"Erro ao deletar subnet {subnet_id}: {e}")

    # 7. Deletar VPC
    try:
        ec2_client.delete_vpc(VpcId=vpc_id)
        logger.info(f"VPC {vpc_id} deletada")
    except Exception as e:
        logger.warning(f"Erro ao deletar VPC: {e}")

    logger.info("=== VPC destruída ===")
