# ============================================================
# Camada Analítica: AWS Glue + Amazon Athena
# ============================================================

data "aws_iam_role" "lab_role" {
  name = var.iam_role_name
}

# ── Glue Database ─────────────────────────────────────────────────────────────

resource "aws_glue_catalog_database" "analytics" {
  name        = "dijkfood_analytics"
  description = "Database analítico DijkFood — eventos operacionais"
}

# ── Glue Table: eventos particionados por data/hora ──────────────────────────
# O Firehose escreve arquivos GZIP/JSON neste layout de partição.
# A tabela usa OpenXJsonSerDe para ler JSON comprimido nativo.

resource "aws_glue_catalog_table" "events" {
  database_name = aws_glue_catalog_database.analytics.name
  name          = "events"
  description   = "Eventos operacionais DijkFood (ORDER_CREATED, STATUS_CHANGED, POSITION_UPDATE)"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification       = "json"
    compressionType      = "gzip"
    has_encrypted_data   = "false"
    "projection.enabled" = "false"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data_lake.bucket}/events/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format"  = "1"
        "ignore.malformed.json" = "TRUE"
      }
    }

    columns {
      name = "event_type"
      type = "string"
    }
    columns {
      name = "order_id"
      type = "string"
    }
    columns {
      name = "customer_id"
      type = "string"
    }
    columns {
      name = "restaurant_id"
      type = "string"
    }
    columns {
      name = "courier_id"
      type = "string"
    }
    columns {
      name = "old_status"
      type = "string"
    }
    columns {
      name = "new_status"
      type = "string"
    }
    columns {
      name = "latitude"
      type = "double"
    }
    columns {
      name = "longitude"
      type = "double"
    }
    columns {
      name = "estimated_time"
      type = "double"
    }
    columns {
      name = "user_message"
      type = "string"
    }
    columns {
      name = "bot_response"
      type = "string"
    }
    columns {
      name = "timestamp"
      type = "string"
    }
    columns {
      name = "region"
      type = "string"
    }
    columns {
      name = "hour_of_day"
      type = "int"
    }
    columns {
      name = "day_of_week"
      type = "int"
    }
  }

  partition_keys {
    name = "year"
    type = "int"
  }
  partition_keys {
    name = "month"
    type = "int"
  }
  partition_keys {
    name = "day"
    type = "int"
  }
  partition_keys {
    name = "hour"
    type = "int"
  }
}

# ── Glue Crawler: descobre novas partições no S3 automaticamente ──────────────

resource "aws_glue_crawler" "events" {
  name          = "${var.project}-crawler"
  role          = data.aws_iam_role.lab_role.arn
  database_name = aws_glue_catalog_database.analytics.name
  description   = "Crawler para descobrir schema dos eventos no data lake"

  s3_target {
    path = "s3://${aws_s3_bucket.data_lake.bucket}/events/"
  }

  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }

  recrawl_policy {
    recrawl_behavior = "CRAWL_EVERYTHING"
  }

  depends_on = [aws_glue_catalog_table.events]
}

# ── Athena Workgroup: centraliza configuração de output ───────────────────────

resource "aws_athena_workgroup" "main" {
  name          = "${var.project}-workgroup"
  description   = "Workgroup principal DijkFood"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
