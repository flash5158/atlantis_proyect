"""Entrega persistente del bus de agentes.

El bus era fire-and-forget: si un agente publicaba mientras el otro
estaba desconectado, el mensaje se perdía (la difusión WebSocket solo
llega a quien está conectado en ese instante). Ahora:

- Cada mensaje del bus ya queda en SQLite (tabla ``agent_bus``) con
  ``seq`` autoincremental global: es estrictamente creciente, luego
  también es monótono dentro de cada equipo.
- Cada agente de cada equipo lleva un cursor (tabla ``bus_cursors``):
  al (re)conectar recibe los mensajes de SU equipo con
  ``seq > last_seq``, en orden, y el cursor avanza al máximo
  entregado. Sin duplicados, sin pérdidas por desconexión.
- Retención: se conservan los últimos 200 mensajes por equipo; al
  escribir se podan los más viejos para que la BD no crezca sin fin.

El alcance al equipo se preserva: un equipo nunca recibe mensajes de
otro (filtro por ``equipo_id`` en cada consulta).
"""

from __future__ import annotations

from sqlalchemy import func

from backend import db
from backend.models import AgentMessage, BusCursor

#: Mensajes conservados por equipo (los más recientes). Se poda al escribir.
RETENCION_POR_EQUIPO = 200


def _eq_equipo(equipo_id: str):
    """Filtro SQL: mensajes cuyo equipo_id (en el JSON) es el dado.

    Los mensajes sin equipo_id se tratan como equipo "" (bus general)."""
    return func.coalesce(AgentMessage.data["equipo_id"].as_string(), "") == equipo_id


def _para_agente(nombre: str):
    """Filtro SQL: mensajes dirigidos al agente o a todos (igual que
    el inbox: comparación insensible a mayúsculas)."""
    n = nombre.lower()
    para = func.coalesce(func.lower(AgentMessage.data["para"].as_string()), "")
    return (para == "") | (para == n) | (para == "todos")


def cursor_de(equipo_id: str, agente: str) -> int:
    """Último seq entregado a (equipo_id, agente). 0 si nunca conectó."""
    eq = (equipo_id or "").strip()
    ag = (agente or "").strip().lower()
    with db.session_scope() as s:
        cur = s.get(BusCursor, (eq, ag))
        return int(cur.last_seq) if cur is not None else 0


def conectar(equipo_id: str, agente: str) -> dict:
    """(Re)conexión de un agente al bus de su equipo.

    Devuelve los mensajes perdidos (``seq > last_seq``, en orden) y
    avanza el cursor al máximo seq entregado. La segunda llamada sin
    mensajes nuevos devuelve lista vacía (sin duplicados).
    """
    eq = (equipo_id or "").strip()
    ag = (agente or "").strip().lower()
    if not eq:
        raise ValueError("equipo_id requerido")
    if not ag:
        raise ValueError("agente requerido")
    with db.session_scope() as s:
        cur = s.get(BusCursor, (eq, ag))
        ultimo = int(cur.last_seq) if cur is not None else 0
        filas = (
            s.query(AgentMessage)
            .filter(_eq_equipo(eq), AgentMessage.seq > ultimo, _para_agente(ag))
            .order_by(AgentMessage.seq.asc())
            .all()
        )
        mensajes = []
        max_seq = ultimo
        for f in filas:
            d = dict(f.data or {})
            d["seq"] = int(f.seq)
            mensajes.append(d)
            if int(f.seq) > max_seq:
                max_seq = int(f.seq)
        if cur is None:
            s.add(BusCursor(equipo_id=eq, agente=ag, last_seq=max_seq))
        else:
            cur.last_seq = max_seq
        return {"mensajes": mensajes, "ultimo_seq": max_seq,
                "entregados": len(mensajes), "equipo_id": eq}


def podar(equipo_id: str) -> int:
    """Elimina los mensajes del equipo más viejos que los últimos
    ``RETENCION_POR_EQUIPO``. Retorna cuántos borró."""
    eq = (equipo_id or "").strip()
    with db.session_scope() as s:
        seqs = [
            r[0]
            for r in s.query(AgentMessage.seq)
            .filter(_eq_equipo(eq))
            .order_by(AgentMessage.seq.desc())
            .all()
        ]
        if len(seqs) <= RETENCION_POR_EQUIPO:
            return 0
        viejos = seqs[RETENCION_POR_EQUIPO:]
        n = (
            s.query(AgentMessage)
            .filter(AgentMessage.seq.in_(viejos))
            .delete(synchronize_session=False)
        )
        return int(n)
