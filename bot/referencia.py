"""El patron de medida: que da entrar al azar en el mismo activo.

Es la herramienta que le falta a casi todos los bots, y la que hubiera ahorrado
la mitad del trabajo de este proyecto.

LA CUENTA. En un mercado sin memoria, la probabilidad de tocar +RR*X antes de -X
es exactamente 1/(1+RR). Con RR 1:2 son 33.3% y con 1:3 son 25%, que es justo el
acierto que hace falta para empatar sin costos. Conclusion: en un paseo aleatorio
CUALQUIER combinacion de stop y objetivo empata antes de comisiones y pierde
despues.

Medido sobre 71 dias de oro en M5, entrando al azar: 33.5% de acierto con RR 1:2
y 24.9% con RR 1:3. La teoria dice 33.3% y 25%. Coincide con dos decimales, o sea
que a 5 minutos de plazo el oro es, para este fin, indistinguible de una moneda.

De ahi sale el unico criterio que sirve para juzgar una estrategia:

    resultado = (cuanto le gana al azar) - (lo que cuesta operar)

Si tu estrategia acierta lo mismo que el azar, no importa cuantos indicadores
tenga: pierde exactamente lo que cuestan las comisiones. Y si acierta MENOS que
el azar, como pasa con 90 de las 96 variantes del scalper que probe, entonces la
logica esta eligiendo activamente los peores momentos.
"""
import random
import statistics


def al_azar(velas, stop_pct, rr, lado=0, muestras=20000, minutos_max=0,
            minutos_vela=5, costo_pct=0.0, semilla=7):
    """Entra en momentos al azar con el mismo stop y objetivo que tu estrategia.

    lado: 1 solo compras, -1 solo ventas, 0 mitad y mitad.
    minutos_max: 0 sin limite de tiempo.
    """
    if len(velas) < 200 or stop_pct <= 0:
        return None

    limite = (minutos_max // minutos_vela) if minutos_max else 0
    rnd = random.Random(semilla)
    resultados = []
    salidas = {"objetivo": 0, "stop": 0, "tiempo": 0}

    for _ in range(muestras):
        i = rnd.randrange(50, max(51, len(velas) - 60))
        es_compra = (lado == 1) or (lado == 0 and rnd.random() < 0.5)
        entrada = velas[i].cierre
        riesgo = entrada * stop_pct / 100
        if riesgo <= 0:
            continue
        stop = entrada - riesgo if es_compra else entrada + riesgo
        objetivo = entrada + riesgo * rr if es_compra else entrada - riesgo * rr
        costo_r = costo_pct / stop_pct

        for j in range(i + 1, len(velas)):
            v = velas[j]
            golpe_stop = v.minimo <= stop if es_compra else v.maximo >= stop
            golpe_obj = v.maximo >= objetivo if es_compra else v.minimo <= objetivo
            if golpe_stop:
                resultados.append(-1.0 - costo_r)
                salidas["stop"] += 1
                break
            if golpe_obj:
                resultados.append(rr - costo_r)
                salidas["objetivo"] += 1
                break
            if limite and (j - i) >= limite:
                movido = (v.cierre - entrada) if es_compra else (entrada - v.cierre)
                resultados.append(movido / riesgo - costo_r)
                salidas["tiempo"] += 1
                break

    if not resultados:
        return None
    return {
        "n": len(resultados),
        "acierto": sum(1 for r in resultados if r > 0) / len(resultados) * 100,
        "r_op": statistics.fmean(resultados),
        "teorico": 1.0 / (1.0 + rr) * 100,
        "salidas": salidas,
    }


def comparar(ops, velas, rr, costo_pct=0.0, minutos_max=0, minutos_vela=5):
    """Compara una lista de operaciones contra el azar con su mismo stop.

    `ops` son objetos con .riesgo_pct y .r (o tuplas (r, riesgo_pct)).
    Devuelve el veredicto listo para mostrar.
    """
    if not ops:
        return None

    def campo(o, nombre, posicion):
        return getattr(o, nombre) if hasattr(o, nombre) else o[posicion]

    stops = [campo(o, "riesgo_pct", 1) for o in ops]
    erres = [campo(o, "r", 0) for o in ops]
    stop_tipico = statistics.median(stops)

    # 20000 muestras dejan la referencia con un desvio de ~0.4 puntos y tardan
    # 0.03 s, asi que la vara no tiembla. El ruido que queda en la comparacion es
    # el de TU muestra, que con 40 operaciones es de +/-7 puntos.
    ref = al_azar(velas, stop_tipico, rr, muestras=20000, minutos_max=minutos_max,
                  minutos_vela=minutos_vela, costo_pct=costo_pct)
    if not ref:
        return None

    acierto = sum(1 for r in erres if r > 0) / len(erres) * 100
    r_op = statistics.fmean(erres)
    ventaja = acierto - ref["acierto"]

    # Con pocas operaciones no se puede decir nada, y decirlo igual seria peor que
    # no medir. El error tipico de una proporcion cerca de 1/3 es de unos 47/raiz(n)
    # puntos: con 30 operaciones son 8.6 puntos, con 100 son 4.7. Por eso hasta 30
    # no hay veredicto, y de ahi en adelante se exige que la ventaja supere ese
    # error antes de llamarla ventaja.
    minimo = 30
    error = 47.0 / (len(erres) ** 0.5)

    if len(erres) < minimo:
        cuantas = "1 operacion" if len(erres) == 1 else f"{len(erres)} operaciones"
        veredicto = (f"solo {cuantas}: no alcanza para decir nada. Hacen falta "
                     f"{minimo} como minimo, y con esta muestra el margen de error "
                     f"es de {error:.1f} puntos")
    elif abs(ventaja) <= error:
        # El margen se aplica para los dos lados. Una diferencia mas chica que el
        # error no dice nada, ni a favor ni en contra, y afirmar lo contrario
        # seria el mismo error que celebrar un backtest de 20 operaciones.
        veredicto = (f"empata con el azar: la diferencia de {ventaja:+.1f} puntos "
                     f"es menor que el margen de error de {error:.1f}. Lo que "
                     f"ganes se lo lleva la comision")
    elif ventaja > 0 and r_op > 0:
        veredicto = (f"le gana al azar por {ventaja:.1f} puntos, mas que el margen "
                     f"de error de {error:.1f}. Es el unico caso que vale la pena "
                     f"seguir mirando: probalo en otros activos y otro periodo")
    elif ventaja > 0:
        veredicto = (f"acierta {ventaja:.1f} puntos mas que el azar pero igual "
                     f"pierde {abs(r_op):.3f} R por operacion: la comision se "
                     f"come la diferencia")
    else:
        veredicto = (f"PIERDE contra el azar por {abs(ventaja):.1f} puntos, mas "
                     f"que el margen de error de {error:.1f}: la logica esta "
                     f"eligiendo los peores momentos")

    return {
        "ops": len(erres), "stop_tipico": stop_tipico,
        "acierto": acierto, "r_op": r_op,
        "acierto_azar": ref["acierto"], "r_op_azar": ref["r_op"],
        "teorico": ref["teorico"], "ventaja_pp": ventaja,
        "margen_error": error, "muestra_suficiente": len(erres) >= minimo,
        "costo_en_r": costo_pct / stop_tipico if stop_tipico else 0.0,
        "veredicto": veredicto,
    }
