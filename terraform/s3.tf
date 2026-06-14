# ============================================================
# S3 Buckets
# ── data-lake        : eventos do Firehose (particionado por data/hora)
# ── models           : arquivos .pkl dos modelos ML treinados
# ── athena-results   : output das queries Athena
# ── frontend         : HTML/CSS/JS (static website hosting)
# ============================================================

locals {
  account_id = data.aws_caller_identity.current.account_id

  buckets = {
    data_lake      = "${var.project}-data-lake-${local.account_id}"
    models         = "${var.project}-models-${local.account_id}"
    athena_results = "${var.project}-athena-results-${local.account_id}"
    frontend       = "${var.project}-frontend-${local.account_id}"
  }
}

# ── Bucket: Data Lake ─────────────────────────────────────────────────────────

resource "aws_s3_bucket" "data_lake" {
  bucket        = local.buckets.data_lake
  force_destroy = true
  tags          = { Name = local.buckets.data_lake, Purpose = "Data Lake — eventos Kinesis Firehose" }
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "data_lake" {
  bucket                  = aws_s3_bucket.data_lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Bucket: Modelos ML ────────────────────────────────────────────────────────

resource "aws_s3_bucket" "models" {
  bucket        = local.buckets.models
  force_destroy = true
  tags          = { Name = local.buckets.models, Purpose = "Modelos ML (.pkl)" }
}

resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models" {
  bucket = aws_s3_bucket.models.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "models" {
  bucket                  = aws_s3_bucket.models.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Bucket: Resultados Athena ─────────────────────────────────────────────────

resource "aws_s3_bucket" "athena_results" {
  bucket        = local.buckets.athena_results
  force_destroy = true
  tags          = { Name = local.buckets.athena_results, Purpose = "Output queries Athena" }
}

resource "aws_s3_bucket_versioning" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  bucket                  = aws_s3_bucket.athena_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Bucket: Frontend Estático ─────────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket        = local.buckets.frontend
  force_destroy = true
  tags          = { Name = local.buckets.frontend, Purpose = "Frontend estático (static website)" }
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Static website hosting
resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document { suffix = "index.html" }
  error_document { key = "index.html" }
}

# Liberar acesso público para servir o site
resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket     = aws_s3_bucket.frontend.id
  depends_on = [aws_s3_bucket_public_access_block.frontend]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
    }]
  })
}

resource "aws_s3_bucket_cors_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST"]
    allowed_origins = ["*"]
    max_age_seconds = 3000
  }
}
