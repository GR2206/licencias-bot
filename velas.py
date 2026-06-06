# velas.py - Diccionario de Patrones Sniper

def identificar_patrones(df):
    if len(df) < 3:
        return []
    ultima = df.iloc[-1]
    previa = df.iloc[-2]
    antepenultima = df.iloc[-3]

    
    # Cálculos de cuerpo y mechas
    cuerpo = abs(ultima['close'] - ultima['open'])
    mecha_superior = ultima['high'] - max(ultima['open'], ultima['close'])
    mecha_inferior = min(ultima['open'], ultima['close']) - ultima['low']
    es_verde = ultima['close'] > ultima['open']
    es_roja = ultima['close'] < ultima['open']

    patrones = []

    # 1. MARTILLO (Hammer) - LONG
    if mecha_inferior > (cuerpo * 2.5) and mecha_superior < cuerpo * 0.5:
        patrones.append("MARTILLO_ALCISTA")

    # 2. ENVOLVENTE (Engulfing)
    if cuerpo > abs(previa['close'] - previa['open']):
        if es_verde and previa['close'] < previa['open']:
            patrones.append("ENVOLVENTE_ALCISTA")
        elif es_roja and previa['close'] > previa['open']:
            patrones.append("ENVOLVENTE_BAJISTA")

    # 5. MARUBOZU (Vela de fuerza extrema sin mechas)
    # Si el cuerpo es más del 90% del tamaño total de la vela
    tamanio_total = ultima['high'] - ultima['low']
    if tamanio_total > 0 and (cuerpo / tamanio_total) > 0.90:
        if es_verde:
            patrones.append("MARUBOZU_ALCISTA")
        else:
            patrones.append("MARUBOZU_BAJISTA")

    # 3. ESTRELLA DE LA MAÑANA (Morning Star) - LONG
    # Roja Grande -> Doji/Pequeña -> Verde que recupera
    if (antepenultima['close'] < antepenultima['open'] and 
        abs(previa['close'] - previa['open']) < (cuerpo * 0.3) and 
        es_verde):
        patrones.append("ESTRELLA_MAÑANA")

    # 4. ESTRELLA DEL ATARDECER (Evening Star) - SHORT
    if (antepenultima['close'] > antepenultima['open'] and 
        abs(previa['close'] - previa['open']) < (cuerpo * 0.3) and 
        es_roja):
        patrones.append("ESTRELLA_ATARDECER")

    # 5. TRES SOLDADOS (Three White Soldiers) - LONG
    if (es_verde and previa['close'] > previa['open'] and 
        antepenultima['close'] > antepenultima['open']):
        patrones.append("TRES_SOLDADOS")

    return patrones