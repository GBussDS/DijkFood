import boto3
import json

s3 = boto3.client('s3')
bucket = 'dijkfood-frontend-884474267760'

print(f"Configurando Static Website Hosting no bucket {bucket}...")
s3.put_bucket_website(
    Bucket=bucket,
    WebsiteConfiguration={
        'IndexDocument': {'Suffix': 'index.html'},
        'ErrorDocument': {'Key': 'index.html'}
    }
)

print("Desativando Block Public Access...")
s3.delete_public_access_block(Bucket=bucket)

print("Aplicando política de leitura pública (s3:GetObject)...")
policy = {
    "Version": "2012-10-17",
    "Statement": [{
        "Sid": "PublicReadGetObject",
        "Effect": "Allow",
        "Principal": "*",
        "Action": "s3:GetObject",
        "Resource": f"arn:aws:s3:::{bucket}/*"
    }]
}
s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))

print("Pronto! Acesse o link novamente.")
