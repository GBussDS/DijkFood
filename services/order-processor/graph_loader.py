"""
DijkFood — Order Processor: Graph Loader
Carrega e gerencia o grafo viário de São Paulo (osmnx) para cálculo de rotas via Dijkstra.
"""
import os
import logging
import networkx as nx

logger = logging.getLogger(__name__)

# Grafo carregado em memória (variável global)
G = None
GRAPH_LOADED = False


def load_graph():
    """
    Carrega o grafo de São Paulo. Tenta carregar de arquivo .graphml local primeiro.
    Se não existir, baixa via osmnx e salva para uso futuro.
    """
    global G, GRAPH_LOADED
    import osmnx as ox

    graphml_path = os.environ.get("GRAPH_PATH", "/app/sp_graph.graphml")

    if os.path.exists(graphml_path):
        logger.info(f"Carregando grafo de {graphml_path}...")
        G = ox.load_graphml(graphml_path)
    else:
        logger.info("Arquivo .graphml não encontrado. Baixando grafo de São Paulo via osmnx...")
        G = ox.graph_from_place("São Paulo, Brazil", network_type="drive")
        os.makedirs(os.path.dirname(graphml_path) if os.path.dirname(graphml_path) else ".", exist_ok=True)
        ox.save_graphml(G, graphml_path)
        logger.info(f"Grafo salvo em {graphml_path}")

    # Force maxspeed to string or delete it to avoid TypeError in pandas/osmnx
    for u, v, k, data in G.edges(keys=True, data=True):
        if "maxspeed" in data:
            del data["maxspeed"]

    # Adicionar velocidades e tempos de viagem às arestas (usando routing.x por deprecation)
    import osmnx.routing as routing
    G = routing.add_edge_speeds(G)
    G = routing.add_edge_travel_times(G)

    GRAPH_LOADED = True
    logger.info(f"Grafo carregado: {G.number_of_nodes()} nós, {G.number_of_edges()} arestas")
    return G


def calculate_route(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float):
    """
    Calcula a rota mais curta entre dois pontos usando Dijkstra sobre o grafo de SP.

    Returns:
        tuple: (route_coords, total_time_seconds)
            - route_coords: lista de [lat, lon] da rota
            - total_time_seconds: tempo total estimado em segundos
    """
    import osmnx as ox

    if G is None:
        raise RuntimeError("Grafo não carregado. Chame load_graph() primeiro.")

    # Encontrar nós mais próximos
    orig_node = ox.nearest_nodes(G, origin_lon, origin_lat)
    dest_node = ox.nearest_nodes(G, dest_lon, dest_lat)

    # Dijkstra com peso = travel_time
    try:
        route = nx.shortest_path(G, orig_node, dest_node, weight="travel_time")
        route_coords = [[G.nodes[n]["y"], G.nodes[n]["x"]] for n in route]
        total_time = nx.shortest_path_length(G, orig_node, dest_node, weight="travel_time")
        return route_coords, total_time
    except nx.NetworkXNoPath:
        logger.warning(f"Sem caminho entre ({origin_lat},{origin_lon}) e ({dest_lat},{dest_lon})")
        # Fallback: rota direta
        return [[origin_lat, origin_lon], [dest_lat, dest_lon]], 1800.0  # 30min default


def is_graph_loaded() -> bool:
    return GRAPH_LOADED
