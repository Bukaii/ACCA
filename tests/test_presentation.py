from acca_stats.presentation import build_general, GENERAL, PLAYERS

def test_general_and_player_registry_views():
    matches=[{"MATCH_ID":"LA2_1","RESULTADO":"VICTORIA","INTEGRANTES":"A|B|C|D|E"}]
    general=build_general(matches)
    assert len(general)==2
    assert set(GENERAL)=={"RESUMEN","PARTIDAS","REGISTRO"}
    assert set(PLAYERS)=={"JUGADORES"}
