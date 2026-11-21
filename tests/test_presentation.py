from acca_stats.presentation import build_general, build_players, GENERAL, PLAYERS

def test_separate_general_and_player_views():
    matches=[{"MATCH_ID":"LA2_1","RESULTADO":"VICTORIA","INTEGRANTES":"A|B|C|D|E"}]
    players=[{"MATCH_ID":"LA2_1","PUUID":"p1","ES_ACCA":"SI","EQUIPO_ACCA":"SI",
              "ALIAS":"A","CAMPEON":"Ahri","ROL":"MIDDLE","ITEMS":"1055|0|1055",
              "KILLS":4,"DEATHS":2,"ASSISTS":7,"VISION":21}]
    details=[{"MATCH_ID":"LA2_1","PUUID":"p1","KDA":5.5,"KP_%":80,"CS_MIN":7}]
    general=build_general(matches)
    ranking,history=build_players(matches,players,details)
    assert len(general)==2
    assert len(history)==1 and history[0][8]==5.5
    assert any(r[0]=="JUGADORES" for r in ranking)
    assert sum(r[0]=="OBJETOS" for r in ranking)==1
    assert set(GENERAL)=={"RESUMEN","PARTIDAS","REGISTRO"}
    assert set(PLAYERS)=={"JUGADORES","RENDIMIENTO","PARTICIPACIONES"}
