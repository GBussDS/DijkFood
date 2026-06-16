data "aws_iam_role" "lab_role" {
  name = var.iam_role_name
}

resource "aws_glue_catalog_database" "analytics" {
  name        = "dijkfood_analytics"
  description = "Database analítico DijkFood — eventos operacionais"
}

resource "aws_glue_catalog_table" "events" {
  database_name = aws_glue_catalog_database.analytics.name
  name          = "events"
  description   = "Eventos operacionais DijkFood"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"                          = "TRUE"
    "classification"                    = "json"
    "compressionType"                   = "gzip"
    "has_encrypted_data"                = "false"
    "projection.enabled"                = "true"
    "projection.datehour.type"          = "date"
    "projection.datehour.format"        = "yyyy/MM/dd/HH"
    "projection.datehour.range"         = "2024/01/01/00,NOW"
    "projection.datehour.interval"      = "1"
    "projection.datehour.interval.unit" = "HOURS"
    "storage.location.template"         = "s3://${aws_s3_bucket.data_lake.bucket}/events/$${datehour}/"
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
    name = "datehour"
    type = "string"
  }
}

resource "aws_athena_workgroup" "main" {
  name          = "${var.project}-workgroup"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}
