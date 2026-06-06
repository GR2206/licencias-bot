# velas.py - Diccionario de patrones Sniper para gatillos M15

EPSILON = 1e-12


def _rango(vela):
    return max(float(vela["high"]) - float(vela["low"]), EPSILON)


def _cuerpo(vela):
    return abs(float(vela["close"]) - float(vela["open"]))


def _mecha_superior(vela):
    return float(vela["high"]) - max(float(vela["open"]), float(vela["close"]))


def _mecha_inferior(vela):
    return min(float(vela["open"]), float(vela["close"])) - float(vela["low"])


def _verde(vela):
    return float(vela["close"]) > float(vela["open"])


def _roja(vela):
    return float(vela["close"]) < float(vela["open"])


def _cuerpo_fuerte(vela, minimo=0.55):
    return _cuerpo(vela) / _rango(vela) >= minimo


def _cuerpo_chico(vela, maximo=0.30):
    return _cuerpo(vela) / _rango(vela) <= maximo


def _punto_medio(vela):
    return (float(vela["open"]) + float(vela["close"])) / 2


def _cerca(valor_a, valor_b, tolerancia_pct=0.0015):
    referencia = max(abs(float(valor_b)), EPSILON)
    return abs(float(valor_a) - float(valor_b)) / referencia <= tolerancia_pct


def _tendencia_reciente(df, direccion, velas=5):
    if len(df) < velas + 1:
        return False

    inicio = float(df["close"].iloc[-velas - 1])
    fin = float(df["close"].iloc[-2])

    if inicio <= 0:
        return False

    cambio = (fin - inicio) / inicio
    if direccion == "ALCISTA":
        return cambio > 0.004
    if direccion == "BAJISTA":
        return cambio < -0.004
    return False


def identificar_patrones(df):
    if len(df) < 3:
        return []

    ultima = df.iloc[-1]
    previa = df.iloc[-2]
    antepenultima = df.iloc[-3]

    patrones = []

    cuerpo = _cuerpo(ultima)
    rango = _rango(ultima)
    mecha_sup = _mecha_superior(ultima)
    mecha_inf = _mecha_inferior(ultima)
    es_verde = _verde(ultima)
    es_roja = _roja(ultima)
    cuerpo_prev = _cuerpo(previa)

    tendencia_alcista = _tendencia_reciente(df, "ALCISTA")
    tendencia_bajista = _tendencia_reciente(df, "BAJISTA")

    # Indecision util para no confundir dojis con velas de fuerza.
    if cuerpo / rango <= 0.10:
        patrones.append("DOJI_INDECISION")

    # Pinbars / martillos: buenos gatillos si aparecen tras barrida o extension.
    if mecha_inf >= cuerpo * 2.0 and mecha_sup <= rango * 0.25 and cuerpo / rango <= 0.45:
        if tendencia_bajista:
            patrones.append("PINBAR_ALCISTA")
            patrones.append("MARTILLO_ALCISTA")
        elif tendencia_alcista:
            patrones.append("HOMBRE_COLGADO_BAJISTA")
        else:
            patrones.append("PINBAR_ALCISTA")

    if mecha_sup >= cuerpo * 2.0 and mecha_inf <= rango * 0.25 and cuerpo / rango <= 0.45:
        if tendencia_alcista:
            patrones.append("PINBAR_BAJISTA")
            patrones.append("ESTRELLA_FUGAZ_BAJISTA")
        elif tendencia_bajista:
            patrones.append("MARTILLO_INVERTIDO_ALCISTA")
        else:
            patrones.append("PINBAR_BAJISTA")

    # Envolventes: el cuerpo actual absorbe el cuerpo previo.
    if cuerpo > cuerpo_prev:
        if (
            es_verde
            and _roja(previa)
            and float(ultima["open"]) <= float(previa["close"])
            and float(ultima["close"]) >= float(previa["open"])
        ):
            patrones.append("ENVOLVENTE_ALCISTA")

        if (
            es_roja
            and _verde(previa)
            and float(ultima["open"]) >= float(previa["close"])
            and float(ultima["close"]) <= float(previa["open"])
        ):
            patrones.append("ENVOLVENTE_BAJISTA")

    # Harami: vela chica dentro del cuerpo previo, posible pausa/reversion.
    cuerpo_dentro_prev = (
        min(float(previa["open"]), float(previa["close"]))
        <= float(ultima["open"])
        <= max(float(previa["open"]), float(previa["close"]))
        and min(float(previa["open"]), float(previa["close"]))
        <= float(ultima["close"])
        <= max(float(previa["open"]), float(previa["close"]))
    )

    if cuerpo_dentro_prev and _cuerpo_chico(ultima, 0.45):
        if _roja(previa) and es_verde:
            patrones.append("HARAMI_ALCISTA")
        elif _verde(previa) and es_roja:
            patrones.append("HARAMI_BAJISTA")

    # Tweezer: doble rechazo en el mismo extremo.
    if _cerca(ultima["low"], previa["low"]) and es_verde and _roja(previa):
        patrones.append("TWEEZER_BOTTOM_ALCISTA")

    if _cerca(ultima["high"], previa["high"]) and es_roja and _verde(previa):
        patrones.append("TWEEZER_TOP_BAJISTA")

    # Piercing / Dark Cloud adaptados a crypto, donde casi no hay gaps reales.
    if (
        _roja(previa)
        and es_verde
        and float(ultima["close"]) > _punto_medio(previa)
        and float(ultima["close"]) < float(previa["open"])
    ):
        patrones.append("PIERCING_LINE_ALCISTA")

    if (
        _verde(previa)
        and es_roja
        and float(ultima["close"]) < _punto_medio(previa)
        and float(ultima["close"]) > float(previa["open"])
    ):
        patrones.append("DARK_CLOUD_COVER_BAJISTA")

    # Marubozu: vela de decision, util como gatillo de continuidad.
    if cuerpo / rango > 0.90:
        if es_verde:
            patrones.append("MARUBOZU_ALCISTA")
        elif es_roja:
            patrones.append("MARUBOZU_BAJISTA")

    # Estrellas de 3 velas: giro tras vela fuerte, pausa y recuperacion.
    if (
        _roja(antepenultima)
        and _cuerpo_fuerte(antepenultima, 0.50)
        and _cuerpo_chico(previa, 0.35)
        and es_verde
        and float(ultima["close"]) > _punto_medio(antepenultima)
    ):
        patrones.append("ESTRELLA_MAÑANA")

    if (
        _verde(antepenultima)
        and _cuerpo_fuerte(antepenultima, 0.50)
        and _cuerpo_chico(previa, 0.35)
        and es_roja
        and float(ultima["close"]) < _punto_medio(antepenultima)
    ):
        patrones.append("ESTRELLA_ATARDECER")

    # Secuencias de fuerza. Exigen cierres progresivos y cuerpos decentes.
    if (
        es_verde
        and _verde(previa)
        and _verde(antepenultima)
        and float(ultima["close"]) > float(previa["close"]) > float(antepenultima["close"])
        and _cuerpo_fuerte(ultima, 0.45)
        and _cuerpo_fuerte(previa, 0.45)
    ):
        patrones.append("TRES_SOLDADOS")

    if (
        es_roja
        and _roja(previa)
        and _roja(antepenultima)
        and float(ultima["close"]) < float(previa["close"]) < float(antepenultima["close"])
        and _cuerpo_fuerte(ultima, 0.45)
        and _cuerpo_fuerte(previa, 0.45)
    ):
        patrones.append("TRES_CUERVOS_NEGROS")

    # Inside bar: compresion previa a expansion, no direccional por si sola.
    if float(ultima["high"]) < float(previa["high"]) and float(ultima["low"]) > float(previa["low"]):
        patrones.append("INSIDE_BAR")

    return list(dict.fromkeys(patrones))