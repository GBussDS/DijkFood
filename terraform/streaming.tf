# ============================================================
# Pipeline de Streaming: Kinesis → Firehose → S3
#                        Kinesis → Lambda (Anomaly Detector)
# ============================================================

# ── Kinesis Data Stream ───────────────────────────────────────────────────────

resource "aws_kinesis_stream" "events" {
  name             = "${var.project}-events"
  shard_count      = var.kinesis_shard_count
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = { Name = "${var.project}-events" }
}

# ── Kinesis Firehose → S3 Data Lake ──────────────────────────────────────────
# Tenta conversão Parquet; a AWS exige buffer >= 64 MB para isso.
# Como o laboratório não gera esse volume, usamos GZIP como fallback
# (espelhando o comportamento do infra/streaming.py).

resource "aws_kinesis_firehose_delivery_stream" "to_s3" {
  name        = "${var.project}-firehose"
  destination = "extended_s3"

  kinesis_source_configuration {
    kinesis_stream_arn = aws_kinesis_stream.events.arn
    role_arn           = data.aws_iam_role.lab_role.arn
  }

  extended_s3_configuration {
    role_arn   = data.aws_iam_role.lab_role.arn
    bucket_arn = aws_s3_bucket.data_lake.arn

    # Particionamento temporal idêntico ao infra/streaming.py
    prefix              = "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/"
    error_output_prefix = "errors/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/!{firehose:error-output-type}/"

    buffering_size     = 5
    buffering_interval = 60
    compression_format = "GZIP"

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = "/aws/firehose/${var.project}"
      log_stream_name = "S3Delivery"
    }
  }

  tags = { Name = "${var.project}-firehose" }
}

# ── Lambda: Anomaly Detector ──────────────────────────────────────────────────

data "archive_file" "anomaly_detector" {
  type        = "zip"
  source_file = "${path.module}/../lambda/anomaly_detector/handler.py"
  output_path = "${path.module}/.lambda_zips/anomaly_detector.zip"
}

resource "aws_lambda_function" "anomaly_detector" {
  function_name    = "${var.project}-anomaly-detector"
  description      = "Detecta anomalias operacionais em eventos do Kinesis"
  filename         = data.archive_file.anomaly_detector.output_path
  source_code_hash = data.archive_file.anomaly_detector.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  role             = data.aws_iam_role.lab_role.arn
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      ANOMALY_TABLE    = aws_dynamodb_table.anomalies.name
      HISTORICAL_TABLE = aws_dynamodb_table.historical_averages.name
    }
  }

  tags = { Name = "${var.project}-anomaly-detector" }
}

# Event Source Mapping: Kinesis → Lambda (batch de 100, fator 2 para paralelismo)
resource "aws_lambda_event_source_mapping" "kinesis_to_anomaly" {
  event_source_arn               = aws_kinesis_stream.events.arn
  function_name                  = aws_lambda_function.anomaly_detector.arn
  starting_position              = "LATEST"
  batch_size                     = 100
  parallelization_factor         = 2
  bisect_batch_on_function_error = true
  enabled                        = true
}

# ── Lambda: Firehose Transform (enriquece eventos com região geográfica) ──────

data "archive_file" "firehose_transform" {
  type        = "zip"
  source_file = "${path.module}/../lambda/firehose_transform/handler.py"
  output_path = "${path.module}/.lambda_zips/firehose_transform.zip"
}

resource "aws_lambda_function" "firehose_transform" {
  function_name    = "${var.project}-firehose-transform"
  description      = "Enriquece eventos com região geográfica antes de gravar no S3"
  filename         = data.archive_file.firehose_transform.output_path
  source_code_hash = data.archive_file.firehose_transform.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  role             = data.aws_iam_role.lab_role.arn
  timeout          = 60
  memory_size      = 128

  tags = { Name = "${var.project}-firehose-transform" }
}
