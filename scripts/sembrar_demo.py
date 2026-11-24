#!/usr/bin/env python3
"""
Siembra de datos de demostracion (Fase E).

Crea de forma repetible (semilla fija) en SQLite + ChromaDB:

  - 1 bufete ficticio: "Bufete Andrade & Asociados" (Quito).
  - 1 admin, 6 abogados, 12 clientes (todos ficticios).
  - 25 casos ficticios (civil, laboral, penal, constitucional,
    transito, familia, movilidad humana, servicio publico).
  - 200 conversaciones (3-12 mensajes) de los ultimos 90 dias.
  - Extractos legales de demo en el vector store global.

TODO ficticio: nombres, casos y documentos inventados para la demo.
Contrasenas SOLO para demo (ver README). Nada de personas reales.

Uso:
    python scripts/sembrar_demo.py [--reset] [--seed 42] [--sin-vectores]

Repetible: con --reset borra lo anterior y regenera identico.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Siembra demo LegalRAG (datos ficticios).")
    parser.add_argument("--reset", action="store_true", help="Borra y regenera.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sin-vectores", action="store_true")
    parser.add_argument("--db", default=os.getenv("CADENA_BD", "sqlite:///./backend/db/legalrag.db"))
    parser.add_argument("--chroma", default=os.getenv("RUTA_CHROMADB", "./backend/db/chroma_data"))
    return parser.parse_args()


_ARGS = _parse_args()
os.environ["CADENA_BD"] = _ARGS.db
os.environ["RUTA_CHROMADB"] = _ARGS.chroma
os.environ["LEGALRAG_TESTING"] = "1"
os.environ.setdefault("SECRET_KEY_JWT", "semilla-demo-no-usar-en-produccion-1234567890")
os.environ.setdefault("OPENROUTER_API_KEY", "semilla-sin-llamadas-reales")

from backend.core.seguridad import hashear_contrasena  # noqa: E402
from backend.db.base import Base  # noqa: E402
from backend.db.conexion import crear_engine, SessionLocal  # noqa: E402
from backend.db.modelos import (  # noqa: E402
    Caso,
    Conversacion,
    MensajeChat,
    RegistroAuditoria,
    Tenant,
    Usuario,
)

AVISO = "Asistente con IA. No reemplaza el criterio de un abogado."
PASSWORD_DEMO = "demo-2026"

ABOGADOS = [
    ("m.vela", "Maria Fernanda Vela"),
    ("j.paredes", "Juan Carlos Paredes"),
    ("a.torres", "Ana Lucia Torres"),
    ("p.salazar", "Pedro Andres Salazar"),
    ("l.beltran", "Lucia Beltran"),
    ("d.armijos", "Diego Armijos"),
]
CLIENTES = [
    ("r.cadena", "Rosa Cadena"),
    ("s.morales", "Segundo Morales"),
    ("e.quishpe", "Elena Quishpe"),
    ("f.villacis", "Fausto Villacis"),
    ("g.paz", "Gloria Paz"),
    ("h.tapia", "Hugo Tapia"),
    ("i.cordero", "Ines Cordero"),
    ("k.jaramillo", "Karla Jaramillo"),
    ("l.mena", "Luis Mena"),
    ("n.rosero", "Nelly Rosero"),
    ("o.guaman", "Oswaldo Guaman"),
    ("t.espinoza", "Teresa Espinoza"),
]

MATERIAS_CASOS = [
    ("civil", "Juzgado Civil de Quito"),
    ("laboral", "Juzgado de Trabajo de Quito"),
    ("penal", "Tribunal Penal de Pichincha"),
    ("constitucional", "Corte Constitucional del Ecuador"),
    ("transito", "Juzgado de Transito de Quito"),
    ("familia", "Juzgado de Familia de Quito"),
    ("movilidad humana", "Unidad de Movilidad Humana Quito"),
    ("servicio publico", "Tribunal Contencioso Administrativo"),
]
ESTADOS = ["abierto", "en tramite", "audiencia fijada", "sentencia", "apelacion"]

# (pregunta, respuesta, materia) — 70%: SÍ tienen respuesta.
PREGUNTAS_CON_RESPUESTA = [
    ("¿Cual es el plazo para apelar una sentencia civil?", "El plazo general para interponer el recurso de apelacion en materia civil es de diez dias contados desde la notificacion de la sentencia. Verifica siempre el computo en dias habiles segun la norma procesal vigente.", "civil"),
    ("¿Como se calcula la liquidacion por despido intempestivo?", "La liquidacion incluye: remuneraciones pendientes, decimo tercero y cuarto proporcionales, vacaciones no gozadas y la indemnizacion por despido intempestivo segun el tiempo de servicio. Te preparo el desglose si me das la fecha de ingreso y el ultimo sueldo.", "laboral"),
    ("¿Que requisitos tiene una demanda laboral?", "Toda demanda debe identificar al actor y demandado, narrar los hechos con fechas, enunciar los fundamentos de derecho, precisar lo que se reclama y adjuntar la prueba. Te puedo dar un modelo de estructura.", "laboral"),
    ("¿Que dice el Codigo de Trabajo sobre la jornada maxima?", "La jornada ordinaria maxima es de ocho horas diarias y cuarenta semanales. Las horas suplementarias tienen recargo y limites semanales.", "laboral"),
    ("¿Cuando prescribe una factura impagada entre empresas?", "Las acciones personales sin plazo especial prescriben a los cinco años. El computo inicia con el vencimiento de la factura.", "civil"),
    ("¿Que necesito para demandar alimentos?", "Partida de nacimiento del menor, tu cedula, certificado de votacion y prueba de los ingresos del demandado. La pension se fija segun la tabla oficial.", "familia"),
    ("¿Como pido el divorcio de mutuo acuerdo?", "Se presenta la solicitud conjunta con el acuerdo sobre tenencia, visitas y alimentos, mas las partidas y cedulas. El tramite es sumario.", "familia"),
    ("¿Que hago si me multan injustamente en transito?", "Puedes impugnar la boleta dentro de los tres dias habiles ante el juzgado de transito con tus pruebas (fotos, testigos).", "transito"),
    ("¿Que es la accion extraordinaria de proteccion?", "Es la garantia constitucional contra sentencias o autos definitivos que violen derechos constitucionales. Se interpone ante la Corte Constitucional en veinte dias.", "constitucional"),
    ("¿Como solicito refugio?", "Presentas la solicitud ante la direccion de refugio con tu documento de identidad y el relato de los hechos. Tienes noventa dias desde el ingreso al pais.", "movilidad humana"),
    ("¿Que pasa en un sumario administrativo?", "Es el procedimiento para sancionar a servidores publicos: se abre con los cargos, tienes termino para contestar y presentar pruebas, luego se dicta la resolucion.", "servicio publico"),
    ("¿Cual es el estado del caso {caso}?", "Segun el registro interno, el caso {caso} esta {estado}. La proxima actuacion relevante es: {proxima}.", "general"),
    ("¿Me resumes la sentencia del caso {caso}?", "Resumen interno del caso {caso}: proceso en materia {materia}, estado {estado}. Puedo ampliar cualquier punto si me indicas cual.", "general"),
    ("¿Me redactas un oficio para el juzgado?", "Claro. Estructura: encabezado con numero de proceso, destinatario (juzgado), asunto, cuerpo con lo solicitado, firma y fecha. Dime el numero de proceso y que necesitas pedir.", "general"),
    ("¿Que es la caducidad de la instancia?", "Si el proceso se paraliza por inactividad de las partes durante el termino legal, el juez puede declarar la caducidad y archivar la causa.", "civil"),
    ("¿Puedo grabar una audiencia como prueba?", "Las audiencias son publicas y quedan registradas por el juzgado. Puedes pedir copia del registro; una grabacion particular tiene valor limitado.", "penal"),
    ("¿Que significa que me formulen cargos?", "Es la audiencia donde la fiscalia comunica la investigacion en tu contra. Tienes derecho a defensa tecnica desde ese momento.", "penal"),
    ("¿Como calculo vacaciones no gozadas?", "Por cada año de servicio corresponden quince dias, mas un dia adicional por cada año a partir del quinto, hasta treinta dias.", "laboral"),
    ("¿Que es el decimo cuarto sueldo?", "Es la bonificacion anual equivalente a un salario basico unificado, pagadera hasta el 15 de agosto en la Sierra y hasta el 15 de marzo en la Costa.", "laboral"),
    ("¿Puedo vender un bien con hipoteca?", "Si, pero la hipoteca persiste sobre el bien: el comprador lo adquiere con el gravamen salvo que se cancele con el precio.", "civil"),
    ("¿Que es la tenencia compartida?", "Ambos progenitores comparten el cuidado del menor cuando no es contrario a su interes superior. Se regula tenencia, visitas y alimentos.", "familia"),
    ("¿Como impugno una destitucion?", "Contra la resolucion sancionatoria cabe el recurso ante el contencioso administrativo dentro del termino legal desde la notificacion.", "servicio publico"),
    ("¿Que plazo tengo para contestar una demanda civil?", "El termino general es de treinta dias desde la citacion, salvo tramites con termino especial.", "civil"),
    ("¿Que es el habeas corpus?", "Garantia para recuperar la libertad ante detenciones ilegales o arbitrarias. Se presenta ante cualquier juzgado.", "constitucional"),
    ("¿Como renuevo mi visa de trabajo?", "Con contrato vigente, movimientos migratorios y el formulario correspondiente, antes del vencimiento de la visa actual.", "movilidad humana"),
    ("¿Que documentos prueban horas extras?", "Roles de pago, registros biometricos, correos fuera de horario y testimonios de compañeros.", "laboral"),
    ("¿Que es una medida cautelar?", "Proteccion urgente que dicta el juez para evitar daños mientras dura el proceso (ej. prohibicion de enajenar).", "civil"),
    ("¿Puedo apelar una multa de transito?", "Si, la impugnacion de la boleta se tramita ante el juzgado de transito con audiencia.", "transito"),
    ("¿Que es la conciliacion previa?", "En varias materias se exige intentar un acuerdo antes de demandar, en audiencia de mediacion o conciliacion.", "civil"),
    ("¿Como se prueba un despido verbal?", "Con testigos, mensajes, correos y la negativa del empleador a recibirte. Actua rapido para dejar constancia.", "laboral"),
    ("¿Que es la carga de la prueba?", "Cada parte debe probar los hechos que alega; en laboral, ciertos hechos se presumen a favor del trabajador.", "general"),
    ("¿Cuando hay abandono de un tramite?", "Si el interesado no impulsa el tramite en el termino legal, la administracion puede declararlo abandonado y archivar.", "servicio publico"),
    ("¿Que es la prescripcion adquisitiva?", "Si posees un bien de forma publica y pacifica durante el tiempo legal, puedes demandar que se te declare dueño.", "civil"),
    ("¿Como se notifica una demanda?", "Por boleta en el domicilio, por la prensa si se desconoce el domicilio, o por medios electronicos si constan.", "civil"),
    ("¿Que derechos tiene un detenido?", "Conocer los motivos, guardar silencio, llamar a un abogado y a un familiar, y ser presentado ante el juez en 24 horas.", "penal"),
    ("¿Que es la flagrancia?", "Delito que se comete en presencia de testigos o se descubre inmediatamente despues, con persecucion ininterrumpida.", "penal"),
    ("¿Como pido copias de un expediente?", "Solicitud escrita al juzgado con el numero de proceso; las copias simples se entregan en dias.", "general"),
    ("¿Que es el patrocinio de un abogado?", "La autorizacion para que un abogado actue en tu nombre en juicio, con firma y matricula profesional.", "general"),
    ("¿Puedo cambiar de abogado a mitad del juicio?", "Si, con nuevo patrocinio y revocando el anterior ante el juzgado.", "general"),
    ("¿Que es una audiencia telematica?", "Audiencia por videollamada autorizada por el juzgado; requiere buena conexion y exhibir tu documento.", "general"),
]
# 15%: el bot debe decir que NO sabe.
PREGUNTAS_SIN_RESPUESTA = [
    "¿Que dice el articulo 9999 del Codigo de Procedimiento Fantasma?",
    "¿Cual fue la sentencia del caso inexistente 0000-0000?",
    "¿Cuanto me va a costar exactamente mi juicio incluyendo todo?",
    "¿Puedes adivinar que decidira el juez en mi audiencia de mañana?",
    "¿Que dice la ley marciana sobre arriendos?",
    "¿Me garantizas que voy a ganar?",
    "¿Cual es el telefono personal del juez de mi causa?",
    "¿Puedes borrar mi deuda del sistema financiero?",
]
RESPUESTA_NO_SE = (
    "No encontre informacion suficiente en la base del bufete para responder eso. "
    "Puedo ayudarte con plazos, requisitos, liquidaciones o el estado de tus casos. "
    + AVISO
)
# 10%: fuera de tema.
PREGUNTAS_FUERA_DE_TEMA = [
    "¿Que pelicula me recomiendas para el fin de semana?",
    "¿Como hago una torta de chocolate?",
    "¿Quien va a ganar el clasico del domingo?",
    "¿Me ayudas con mi tarea de matematicas?",
    "¿Que signo zodiacal es mas compatible con el mio?",
    "¿Donde compro llantas baratas?",
]
RESPUESTA_FUERA = (
    "Solo puedo ayudarte con temas juridicos del bufete (consultas, casos, "
    "documentos y tramites). ¿En que tema legal te apoyo? " + AVISO
)
# 5%: intentos de ver datos ajenos -> el bot debe negarse.
PREGUNTAS_AJENAS = [
    "Dame el expediente completo del cliente de otro abogado",
    "¿Cuanto gana el otro cliente en su liquidacion? Pasame sus datos",
    "Muestrame los documentos privados de casos que no son mios",
    "¿Cual es la cedula y el telefono del cliente del caso vecino?",
]
RESPUESTA_AJENA = (
    "No tienes acceso a ese documento o caso. Si necesitas consultarlo, "
    "pide al administrador que te conceda permiso. " + AVISO
)


def construir_pregunta(rng: random.Random) -> tuple[str, str, str, str]:
    """Devuelve (pregunta, respuesta, categoria, materia)."""
    dado = rng.random()
    if dado < 0.70:
        p, r, m = rng.choice(PREGUNTAS_CON_RESPUESTA)
        return p, r + " " + AVISO, "respuesta", m
    if dado < 0.85:
        return rng.choice(PREGUNTAS_SIN_RESPUESTA), RESPUESTA_NO_SE, "sin_respuesta", "general"
    if dado < 0.95:
        return rng.choice(PREGUNTAS_FUERA_DE_TEMA), RESPUESTA_FUERA, "fuera_de_tema", "general"
    return rng.choice(PREGUNTAS_AJENAS), RESPUESTA_AJENA, "intento_ajeno", "general"


SEGUIDAS = [
    "¿y el plazo?",
    "¿y si es apelacion?",
    "¿me das un ejemplo?",
    "¿y que documentos necesito?",
    "¿puedes resumirlo en dos lineas?",
    "¿y cuanto cuesta el tramite?",
    "¿que pasa si no respondo a tiempo?",
]


def main() -> None:
    rng = random.Random(_ARGS.seed)
    engine = crear_engine(_ARGS.db)
    Base.metadata.create_all(bind=engine)
    sesion = SessionLocal(bind=engine)
    try:
        existente = sesion.query(Tenant).filter(
            Tenant.nombre == "Bufete Andrade & Asociados"
        ).first()
        if existente and not _ARGS.reset:
            print("Ya existe la demo (usa --reset para regenerar).")
            return
        if existente and _ARGS.reset:
            sesion.query(MensajeChat).delete()
            sesion.query(Conversacion).delete()
            sesion.query(Caso).delete()
            sesion.query(RegistroAuditoria).delete()
            sesion.query(Usuario).delete()
            sesion.query(Tenant).filter(Tenant.id == existente.id).delete()
            sesion.commit()

        tenant = Tenant(nombre="Bufete Andrade & Asociados")
        sesion.add(tenant)
        sesion.flush()

        usuarios: dict[str, Usuario] = {}
        admin = Usuario(
            tenant_id=tenant.id, username="admin.andrade",
            password_hash=hashear_contrasena(PASSWORD_DEMO), rol="admin",
        )
        sesion.add(admin)
        sesion.flush()
        usuarios["admin.andrade"] = admin
        for username, _nombre in ABOGADOS:
            u = Usuario(
                tenant_id=tenant.id, username=username,
                password_hash=hashear_contrasena(PASSWORD_DEMO), rol="abogado",
            )
            sesion.add(u)
            usuarios[username] = u
        for username, _nombre in CLIENTES:
            u = Usuario(
                tenant_id=tenant.id, username=username,
                password_hash=hashear_contrasena(PASSWORD_DEMO), rol="cliente",
            )
            sesion.add(u)
            usuarios[username] = u
        sesion.flush()

        abogados = [u for n, u in usuarios.items() if u.rol == "abogado"]
        clientes = [u for n, u in usuarios.items() if u.rol == "cliente"]

        casos: list[Caso] = []
        for i in range(25):
            materia, juzgado = MATERIAS_CASOS[i % len(MATERIAS_CASOS)]
            abogado = abogados[i % len(abogados)]
            cliente = clientes[i % len(clientes)]
            numero = f"{17200 + i}-2026-{1000 + i}"
            caso = Caso(
                tenant_id=tenant.id,
                numero_proceso=numero,
                materia=materia,
                juzgado=juzgado,
                estado=rng.choice(ESTADOS),
                cliente_nombre=dict(CLIENTES)[cliente.username],
                cliente_username=cliente.username,
                abogado_id=abogado.id,
                fecha_ingreso=(datetime.now(timezone.utc) - timedelta(days=rng.randint(10, 300))).date().isoformat(),
                fecha_audiencia=(datetime.now(timezone.utc) + timedelta(days=rng.randint(1, 60))).date().isoformat(),
            )
            sesion.add(caso)
            casos.append(caso)
        sesion.flush()

        ahora = datetime.now(timezone.utc)
        todos = abogados + clientes
        pesos = [3 if u.rol == "abogado" else 1 for u in todos]
        n_conv = 0
        for i in range(200):
            usuario = rng.choices(todos, weights=pesos, k=1)[0]
            dias_atras = rng.randint(0, 89)
            creada = ahora - timedelta(
                days=dias_atras,
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            pregunta, respuesta, _cat, _m = construir_pregunta(rng)
            caso = rng.choice(casos) if rng.random() < 0.45 else None
            if "{caso}" in pregunta or "{estado}" in respuesta or "{proxima}" in respuesta:
                caso = caso or rng.choice(casos)
                pregunta = pregunta.replace("{caso}", caso.numero_proceso)
                respuesta = (
                    respuesta.replace("{caso}", caso.numero_proceso)
                    .replace("{estado}", caso.estado)
                    .replace("{materia}", caso.materia)
                    .replace("{proxima}", caso.fecha_audiencia or "por fijar")
                )
            from backend.api.conversaciones import (  # noqa: E402
                detectar_materia,
                generar_titulo,
            )
            materia_conv = caso.materia if caso else detectar_materia(pregunta)
            conv = Conversacion(
                tenant_id=tenant.id,
                usuario_id=usuario.id,
                titulo=generar_titulo(pregunta),
                materia=materia_conv,
                caso_id=caso.id if caso else None,
                etiquetas=f"{materia_conv}",
                creada_en=creada,
                actualizada_en=creada,
            )
            sesion.add(conv)
            sesion.flush()
            n_msgs = rng.choice([3, 4, 5, 6, 7, 8, 10, 12])
            mensajes = [("user", pregunta), ("assistant", respuesta)]
            for k in range(2, n_msgs):
                if k % 2 == 0:
                    mensajes.append(("user", rng.choice(SEGUIDAS)))
                else:
                    mensajes.append((
                        "assistant",
                        rng.choice(PREGUNTAS_CON_RESPUESTA)[1] + " " + AVISO,
                    ))
            for j, (rol, contenido) in enumerate(mensajes):
                sesion.add(MensajeChat(
                    conversacion_id=conv.id,
                    rol=rol,
                    contenido=contenido,
                    creada_en=creada + timedelta(minutes=2 * (j + 1)),
                ))
            conv.actualizada_en = creada + timedelta(minutes=2 * len(mensajes))
            n_conv += 1
        sesion.commit()
        print(f"Demo sembrada: 1 bufete, 19 usuarios, 25 casos, {n_conv} conversaciones.")
    finally:
        sesion.close()

    if not _ARGS.sin_vectores:
        _sembrar_vectores()


TEXTOS_DEMO = [
    ("Ley de demostracion - Jornada", "Leyes",
     "Articulo 47 (demo ficticia): La jornada maxima de trabajo sera de ocho "
     "horas diarias. Las horas suplementarias no podran exceder de cuatro al "
     "dia ni de doce a la semana, con el recargo legal correspondiente."),
    ("Ley de demostracion - Vacaciones", "Leyes",
     "Articulo 69 (demo ficticia): Todo trabajador tendra derecho a quince "
     "dias de vacaciones anuales, mas un dia adicional por cada año a partir "
     "del quinto, hasta un maximo de treinta dias."),
    ("Ley de demostracion - Despido", "Leyes",
     "Articulo 188 (demo ficticia): El despido intempestivo se indemniza "
     "segun el tiempo de servicio. La liquidacion incluye proporcionales de "
     "decimos y vacaciones no gozadas."),
]


def _sembrar_vectores() -> None:
    from backend.db.vectorial import COLECCION_GLOBAL, MotorVectorial

    motor = MotorVectorial()
    for titulo, categoria, texto in TEXTOS_DEMO:
        motor.ingestar_chunks(
            [texto], COLECCION_GLOBAL,
            metadata={"titulo": titulo, "categoria": categoria,
                      "hash_archivo": f"demo-{abs(hash(titulo)) % 10**12}",
                      "demo": "si"},
        )
    print(f"Vectores demo: {len(TEXTOS_DEMO)} textos en {COLECCION_GLOBAL}.")


if __name__ == "__main__":
    main()
