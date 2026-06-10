"""
DijkFood — Lambda: Firehose Transform
Transformação opcional de registros do Kinesis Firehose antes de gravar em S3.
Adiciona campos derivados: region (baseado em lat/lon), enriquecimento de dados.
"""
import base64
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Grid de regiões de SP (simplificado)
SP_REGIONS = {
    "zona_norte": (-23.45, -46.65),
    "zona_sul": (-23.65, -46.65),
    "zona_leste": (-23.55, -46.50),
    "zona_oeste": (-23.55, -46.75),
    "centro": (-23.55, -46.63),
}


def classify_region(lat, lon):
    """Classifica uma coordenada em região de SP."""
    if lat is None or lon is None:
        return "unknown"

    lat = float(lat)
    lon = float(lon)

    min_dist = float("inf")
    closest_region = "unknown"

    for region_name, (ref_lat, ref_lon) in SP_REGIONS.items():
        dist = ((lat - ref_lat) ** 2 + (lon - ref_lon) ** 2) ** 0.5
        if dist < min_dist:
            min_dist = dist
            closest_region = region_name

    return closest_region


def lambda_handler(event, context):
    """
    Handler de transformação do Firehose.
    Cada record é transformado e retornado com resultado 'Ok', 'Dropped', ou 'ProcessingFailed'.
    """
    output = []

    for record in event["records"]:
        try:
            payload = json.loads(base64.b64decode(record["data"]))

            # Enriquecer com região
            lat = payload.get("latitude")
            lon = payload.get("longitude")
            payload["region"] = classify_region(lat, lon)

            # Adicionar campos derivados com base no tipo de evento
            if payload.get("event_type") == "ORDER_CREATED":
                # Extrair hora e dia da semana
                from datetime import datetime
                ts = payload.get("timestamp", "")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        payload["hour_of_day"] = dt.hour
                        payload["day_of_week"] = dt.weekday()
                    except Exception:
                        pass

            # Re-codificar
            encoded = base64.b64encode(
                json.dumps(payload).encode("utf-8")
            ).decode("utf-8")

            output.append({
                "recordId": record["recordId"],
                "result": "Ok",
                "data": encoded
            })

        except Exception as e:
            logger.error(f"Erro ao transformar record: {e}")
            output.append({
                "recordId": record["recordId"],
                "result": "ProcessingFailed",
                "data": record["data"]
            })

    logger.info(f"Processados {len(output)} records de transformação Firehose")
    return {"records": output}
