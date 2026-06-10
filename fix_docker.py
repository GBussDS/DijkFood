import boto3
import json
from infra.compute import build_and_push_images

with open("deploy_state.json") as f:
    state = json.load(f)

ecr = boto3.client("ecr", region_name="us-east-1")
ecs = boto3.client("ecs", region_name="us-east-1")
sts = boto3.client("sts", region_name="us-east-1")

account_id = sts.get_caller_identity()["Account"]

print(">>> Iniciando Build e Push das imagens Docker para o ECR...")
build_and_push_images(ecr, state["ecr_repos"], account_id)

print("\n>>> Forçando recarregamento dos serviços no ECS...")
for svc_name in state["ecr_repos"].keys():
    print(f"  Reiniciando dijkfood-{svc_name}...")
    try:
        ecs.update_service(
            cluster=state["cluster_arn"],
            service=f"dijkfood-{svc_name}",
            forceNewDeployment=True
        )
    except Exception as e:
        print(f"  Erro ao reiniciar {svc_name}: {e}")

print("\n>>> Pronto! O AWS ECS está subindo os containers novos.")
print(">>> Aguarde cerca de 3 minutos e o Dashboard/Chat começará a funcionar!")
