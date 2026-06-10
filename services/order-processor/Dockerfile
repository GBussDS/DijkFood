FROM python:3.12-slim

WORKDIR /app

# Instalar dependências de sistema para osmnx/GDAL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pré-baixar e cachear grafo de SP durante o build
RUN python -c "import osmnx as ox; G = ox.graph_from_place('São Paulo, Brazil', network_type='drive'); ox.save_graphml(G, '/app/sp_graph.graphml')"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
