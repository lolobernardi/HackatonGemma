# Pre-triaje conversacional — FastAPI + Gemma 4 + Streamlit

> Prototipo de hackathon, sin validación clínica. Usar únicamente con casos
> sintéticos. No diagnostica ni reemplaza la evaluación profesional.

## Qué se integró

La interfaz Streamlit **no llama a Gemma directamente**. Se conecta al backend
FastAPI existente y respeta su máquina de estados:

```text
Usuario en Streamlit
        ↓ POST /sesion y POST /mensaje
Backend FastAPI
        ↓
Orquestador de conversación
        ↓
Gemma 4 local vía Ollama → tool call actualizar_ficha
        ↓
Merge incremental de la ficha
        ↓
Pregunta siguiente / bandera roja / motor de reglas
        ↓                                    ↓
        ↓                          color de triaje
        ↓                                    ↓
        ↓                    búsqueda de centros de salud
        ↓                    (ciudad + especialista + horario)
        ↓                                    ↓
        ↓                    PostgreSQL 127.0.0.1:5438
        ↓                                    ↓
Respuesta del backend → Streamlit → banda de triaje al pie
```

Así se conserva la separación correcta:

- **Gemma** interpreta el lenguaje natural y estructura el relato.
- **El orquestador** decide cuándo seguir preguntando o cerrar.
- **El motor determinístico** (`app/reglas.py` + `app/ruleset.yaml`) clasifica en
  los cinco niveles y decide a qué especialista y a qué tipo de efector derivar.
  Ver [`app/REGLAS.md`](app/REGLAS.md).
- **La base de centros** aporta a dónde ir, y no influye en la clasificación.
- **Streamlit** solo presenta la conversación y consume la API.

## Cuando la persona no viene a consultar nada

Alguien puede entrar y decir que está bien. El sistema no tenía cómo
representar eso: `motivo_consulta` quedaba en `None`, se le seguía preguntando
hasta agotar el tope, y se cerraba con el fallback por información incompleta,
que es **amarillo**. "Conviene que te vea un médico hoy" a alguien sano no es
precaución: es un falso positivo que satura el sistema.

Ahora existe un cierre propio, `tipo: "sin_motivo"`, **sin color de triaje**:
no es un nivel de urgencia bajo, es la ausencia de una consulta que priorizar.
Se llega por dos caminos, y los dos exigen que no haya aparecido ningún motivo
y que los **cuatro** discriminadores de riesgo vital estén respondidos y
benignos:

1. La persona lo dijo y Gemma lo registró en `sin_motivo_consulta`.
2. Nunca lo dijo con esas palabras, pero la conversación dejó de avanzar sin
   que apareciera ni un motivo ni un hallazgo.

Exigir los cuatro generales es la red de seguridad: alguien puede decir que
está bien sin registrar que le falta el aire. Mientras no se sepan esos cuatro,
se sigue preguntando, y una bandera roja siempre gana sobre un "estoy bien".

### Conversaciones que no avanzan

Dos arreglos relacionados, del mismo caso reportado:

- **No se repite una pregunta ya hecha.** Se guarda su forma canónica (sin
  tildes ni puntuación) y, ante una repetición, se pasa al fallback del
  siguiente campo faltante. Si no queda ninguna nueva, se cierra: volver a
  preguntar lo mismo no trae una respuesta distinta.
- **Dos turnos seguidos sin ganar un solo campo cierran la conversación**
  (`MAX_TURNOS_SIN_AVANCE`). Hay datos que la persona no tiene —no se tomó la
  temperatura, no quiere decir la edad— y insistir sólo reformula la misma
  pregunta.

## Por qué antes todo salía amarillo

El motor no baja de amarillo mientras le falte información: verde y azul son
afirmaciones fuertes ("podés esperar hasta 4 horas") y exigen evidencia
positiva de benignidad, no ausencia de alarma (etapa 3 en `REGLAS.md`).

Esa evidencia son los **discriminadores específicos** de cada flowchart
(`dolor_opresivo`, `signos_infeccion`, `lesion_antigua_sin_cambios`…). El
orquestador sólo preguntaba los discriminadores *generales*, así que los
específicos nunca se completaban y toda consulta caía en el piso por
ignorancia. Tres cambios lo arreglan:

1. Qué falta lo decide `reglas.campos_requeridos()`, que sí conoce los
   discriminadores de cada flowchart.
2. El bloque de contexto del prompt le dice a Gemma, para el motivo en curso,
   exactamente qué campos puede completar y con qué nombre.
3. `MAX_PREGUNTAS` subió a 10, porque ahora hay más que juntar.

Con eso los cinco colores son alcanzables. Un lunar de larga data sin cambios
sale **azul**; una herida chica sin signos de infección, **verde**.

## Estructura

```text
app/                         backend FastAPI existente
app/centros_db.py            acceso a PostgreSQL (única parte que sabe de SQL)
app/recursos.py              política de búsqueda de centros
frontend/api_client.py       cliente HTTP con errores controlados
frontend/streamlit_app.py    interfaz tipo chat + banda de triaje
iniciar_backend.ps1          arranque del backend
iniciar_frontend.ps1         arranque de Streamlit
preparar_entorno.ps1         entorno virtual + dependencias + .env
*.cmd                        lanzadores que evitan la política de ejecución
.streamlit/config.toml       arranque sin prompt interactivo, solo localhost
../centros_salud_db/         proyecto hermano: la base de centros de salud
```

## Centros de salud: de dónde salen

`app/recursos.py` dejó de ser un STUB con centros hardcodeados. Ahora consulta
la base `centros_salud` (86 efectores de Paraná y Santa Fe capital) del proyecto
hermano `centros_salud_db`, por PostgreSQL en `127.0.0.1:5438`.

La búsqueda entra por los tres criterios que definió el equipo:

- **ciudad** — dónde está la persona. **El frontend se lo pregunta** con un
  desplegable de localidades. Como la base sólo cubre Paraná y Santa Fe, una
  localidad sin centros se resuelve a la ciudad con cobertura más cercana,
  calculada por distancia real (Haversine sobre `config.LOCALIDADES`): Oro
  Verde va a Paraná, Santo Tomé a Santa Fe. Eso **se le avisa a la persona** en
  el mensaje final: no se la manda a otra localidad en silencio. Si además
  llegan coordenadas (`lat`/`lng`), esas mandan sobre la localidad elegida.
- **especialista** — lo decide el motor de reglas, no este módulo:
  `Clasificacion.especialidad_sugerida`. `config.ESPECIALIDAD_DB` sólo traduce
  el slug del ruleset (`cardiologia`) al nombre de la base (`Cardiología`).
- **horario** — la hora de la consulta, para no mandar a alguien a un centro
  cerrado.

El **tipo de efector** también sale del motor
(`Clasificacion.tipo_recurso_sugerido`), y `config.TIPOS_POR_RECURSO` lo
traduce a los tipos de la base. No mandar un azul a una guardia de alta
complejidad es parte del diseño: satura el sistema.

> Antes había un mapa motivo→especialidad acá **y** en el ruleset: dos fuentes
> de verdad para la misma pregunta clínica. Ahora la decisión vive sólo en
> `reglas.py` y acá queda la traducción de vocabulario.

### Especialidades que el ruleset pide y la base no tiene

Dermatología, neumonología, neurología e infectología: **0 centros** de los 86
las declaran. Esas caen en Medicina General, porque ofrecer un centro real es
mejor que no ofrecer ninguno. Hay un test que falla si el ruleset suma una
especialidad y nadie la traduce.

### Por qué la búsqueda es en cascada

La cobertura de la base es despareja: Cardiología está en 2 centros y
Traumatología en 2, contra 44 de Medicina General. Una consulta rígida por
(ciudad + especialidad exacta + tipo + hora) daría vacío casi siempre. Entonces
se prueban filtros de más específico a más amplio y gana el primero que
devuelve algo. El criterio que funcionó viaja hasta el frontend
(`criterio_busqueda`): si a alguien le terminaron ofreciendo un centro de
atención primaria porque no había cardiología cerca, tiene que poder saberlo.

### Límites que vienen de los datos, no del código

- **`distancia_km` siempre es `None`.** La base no tiene las direcciones
  geocodificadas (0 de 86), así que el orden cae en el criterio de respaldo
  (ciudad, nombre). La consulta ya calcula Haversine: cuando se carguen
  lat/lon empieza a ordenar por cercanía sin tocar una línea.
- **`horario` está cargado en 6 de 86 centros.** Por eso el filtro por hora no
  descarta a los que no lo tienen: si lo hiciera, dejaría afuera 80 de 86. Los
  centros sin horario se muestran aclarando "conviene llamar antes de ir",
  porque no es lo mismo que estar cerrado.
- **`ocupacion_estimada` no existe en la base** y quedó en `None`. El STUB la
  inventaba; inventar cuán lleno está un hospital es desinformar sobre dónde ir.

## Cómo se levanta todo

El sistema son **cuatro procesos**. Los tres primeros son requisitos del
cuarto, y van cada uno en su propia terminal (salvo Ollama, que suele quedar
corriendo como servicio).

| # | Proceso | Dónde escucha | Cómo se levanta |
|---|---------|---------------|-----------------|
| 1 | Ollama + Gemma | `127.0.0.1:11434` | `ollama serve` (o la app de Ollama) |
| 2 | PostgreSQL de centros | `127.0.0.1:5438` | `centros_salud_db\start_db.ps1` |
| 3 | Backend FastAPI | `127.0.0.1:8000` | `iniciar_backend.ps1` |
| 4 | Frontend Streamlit | `127.0.0.1:8501` | `iniciar_frontend.ps1` |

Los tres últimos los levanta de una `iniciar_todo.ps1`, en la raíz del
proyecto. Ollama queda afuera a propósito: suele correr como servicio y el
script lo verifica en vez de arrancarlo.

### Todo de una: `iniciar_todo.ps1`

Desde la carpeta **raíz** del proyecto (la que contiene `centros_salud_db\` y
`pretriaje_gemma_streamlit_integrado\`):

```powershell
.\iniciar_todo.ps1
```

O doble clic en `iniciar_todo.cmd`, que hace lo mismo salteando la política de
ejecución.

Abre los tres servicios, **cada uno en su propia ventana** de PowerShell
(tituladas `1/3`, `2/3`, `3/3`), en orden y esperando a que cada uno responda
antes de arrancar el siguiente: el backend consulta la base al levantar y
Streamlit consulta el backend, así que arrancarlos a ciegas produce fallas
intermitentes que parecen del código y son de tiempos.

Antes de abrir nada verifica lo que suele fallar y corta con un mensaje útil:

- que Ollama esté corriendo,
- que el modelo de `.env` esté descargado **en esta computadora** (si no, lista
  los que sí están y da el `ollama pull` exacto),
- que el entorno virtual sirva acá, y si no, lo recrea solo.

También imprime cómo está integrado Gemma: el modelo en uso, que corre local,
y que **no decide la urgencia** — eso lo hace el motor de reglas.

Opciones:

```powershell
.\iniciar_todo.ps1 -Preparar       # fuerza reinstalar el entorno de Python
.\iniciar_todo.ps1 -SinNavegador   # no abre el navegador al terminar
```

> **Sin rutas fijas.** Todo cuelga de `$PSScriptRoot`, la carpeta del propio
> script, así que el proyecto se puede copiar a otra computadora o a otro
> usuario y funciona igual. Lo único que se busca en el sistema es PostgreSQL,
> y eso se resuelve por `PATH` o preguntándole a conda dónde está.

### A mano, uno por uno

```powershell
# 1. Ollama (si no está ya corriendo como servicio)
ollama serve

# 2. Base de centros de salud — desde la carpeta centros_salud_db
cd ..\centros_salud_db
.\start_db.ps1

# 3. Backend — desde pretriaje_gemma_streamlit_integrado, en OTRA terminal
cd ..\pretriaje_gemma_streamlit_integrado
.\iniciar_backend.ps1

# 4. Frontend — en una TERCERA terminal
.\iniciar_frontend.ps1
```

Después se abre `http://localhost:8501`.

Si PowerShell se queja de la firma de los scripts, usá los `.cmd` equivalentes
o mirá la sección 0 más abajo.

### Verificar que está todo arriba

```powershell
curl.exe http://localhost:11434/api/tags     # Ollama
curl.exe http://localhost:8000/health        # Backend (informa el resto)
```

`/health` responde algo así:

```json
{
  "estado": "ok",
  "modelo": "gemma4:12b",
  "ollama": "http://localhost:11434",
  "sesiones_activas": 0,
  "centros_db": true,
  "ciudad_paciente": "Oro Verde"
}
```

`centros_db: false` significa que la base no está levantada. **No es
bloqueante**: el triaje sigue funcionando y la persona recibe igual su nivel de
urgencia y los signos de alarma; lo único que se pierde es la sugerencia de a
qué centro ir. La barra lateral de Streamlit lo avisa.

### Para bajar todo

`Ctrl+C` en las terminales del backend y del frontend, y para la base:

```powershell
cd ..\centros_salud_db
.\stop_db.ps1
```

## Puesta en marcha en Windows

> **El proyecto no se puede copiar entre computadoras con el `.venv` adentro.**
> Un entorno virtual guarda la ruta absoluta del intérprete que lo creó, así que
> el que viaja en un zip / OneDrive queda muerto en la máquina destino. Si lo
> recibiste así, corré `preparar_entorno.ps1`: detecta el `.venv` ajeno y lo
> recrea solo.

### 0. Si PowerShell se queja de la firma del script

```text
iniciar_backend.ps1 is not digitally signed
```

Es la política de ejecución de PowerShell, no un problema del proyecto. Hay
tres salidas:

- Usar los lanzadores `.cmd` (`preparar_entorno.cmd`, `iniciar_backend.cmd`,
  `iniciar_frontend.cmd`), que ya invocan PowerShell con `-ExecutionPolicy Bypass`.
- Correr el `.ps1` puntualmente:
  `powershell -ExecutionPolicy Bypass -File .\iniciar_backend.ps1`
- Habilitarlo para tu usuario, una sola vez:
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 1. Preparar una sola vez

Abrí PowerShell en la raíz del proyecto:

```powershell
.\preparar_entorno.ps1
```

El script crea `.venv`, instala dependencias, copia `.env.example` a `.env` y
avisa si el modelo de `.env` no está bajado en Ollama.

### 2. Elegir el modelo de ESTA computadora

`MODELO` en `.env` tiene que coincidir **exacto** con un tag de `ollama list`:
`/health` compara el string literal y devuelve 503 si no lo encuentra. Cada
equipo del proyecto tiene bajado un tamaño distinto, así que este valor es
personal de cada máquina y no debería viajar en el repo.

```powershell
ollama list
curl.exe http://localhost:11434/api/tags
```

Ajustá `MODELO=` en `.env` con lo que aparezca ahí, o bajá el que quieras usar:

```powershell
ollama pull gemma4:12b
```

> **`GEMMA_THINK` va en `false`.** Los tags con capacidad de *thinking* (como
> `gemma4:12b`) razonan en voz alta por defecto y agotan la salida antes de
> emitir la tool call: el turno termina siempre en `sin_tool_call` y el usuario
> ve el mensaje de error seguro. Con thinking apagado la extracción funciona y
> además baja de ~120 s a ~10 s por turno.

### 3. Iniciar el backend

En una PowerShell:

```powershell
.\iniciar_backend.ps1
```

Comprobación:

```powershell
curl.exe http://localhost:8000/health
```

### 4. Iniciar Streamlit

En otra PowerShell:

```powershell
.\iniciar_frontend.ps1
```

Abrí `http://localhost:8501`.

## Problemas conocidos

### El backend arranca y se cierra solo

Síntoma: la ventana se abre, loguea `app_iniciada` y enseguida `Shutting down`,
en loop. En el log aparece:

```text
WatchFiles detected changes in '.venv\Lib\site-packages\...'. Reloading...
```

El `--reload` de uvicorn vigilaba todo el proyecto, `.venv` incluido. Con el
proyecto dentro de una carpeta sincronizada (OneDrive, Drive, Dropbox), la
sincronización toca archivos de `site-packages` y el servidor se reinicia sin
parar. `iniciar_backend.ps1` ya pasa `--reload-dir app` para vigilar solo el
código propio.

### Streamlit arranca y se cierra sin decir nada

En el primer arranque de cada máquina, Streamlit pide un email por consola
(`Welcome to Streamlit!`) y espera una respuesta. Si nadie contesta, el proceso
termina. `.streamlit/config.toml` lo desactiva con `headless = true`.

Ese mismo archivo deja la app escuchando solo en `localhost`, igual que el
backend. Si necesitás abrirla desde otro dispositivo de la red, comentá la
línea `address`.

### El proyecto vive dentro de OneDrive

Funciona, pero OneDrive va a sincronizar los ~360 MB de `.venv` a la nube y eso
enlentece todo. `.venv/` ya está en `.gitignore`; conviene además excluir esa
carpeta de la sincronización, o mover el proyecto fuera de OneDrive.

### `is not digitally signed`

Ver la sección 0 de la puesta en marcha.

## La ficha clínica

El desplegable "Ficha clínica de la consulta" muestra lo que respondió la
persona como un cuadro agrupado en tres secciones (Paciente, Evaluación
inicial, Detalles del cuadro), con los nombres de campo del ruleset traducidos
a castellano y los valores también: `true` → "Sí", `null` → "**No consultado**".

Esa distinción no es cosmética: un campo sin preguntar no es una respuesta
negativa, y confundirlos es exactamente el error que el motor de reglas evita
(ver "`None` no es `False`" en `REGLAS.md`).

Se arma con tablas de texto y no con `st.dataframe`, que dibuja sobre canvas: si
la ficha la va a leer un profesional, tiene que poder copiarse, imprimirse y
leerse con un lector de pantalla.

## La banda de triaje

El color de severidad se muestra en una **banda fija al pie de la página**,
recién cuando hay un veredicto. Durante la conversación no hay color todavía y
mostrar uno provisional sería engañoso.

El color nunca va solo: la banda dice el nivel (`NIVEL AMARILLO`) y qué
significa en lenguaje llano ("Te tienen que ver hoy"). Un color aislado no le
dice nada a la persona. El amarillo lleva texto oscuro porque sobre fondo claro
no se lee, y el nivel de urgencia es justo lo que no puede quedar ilegible.

Una `derivacion_inmediata` (bandera roja) no trae bloque `resultado` —el
orquestador corta antes de llamar al motor de reglas— pero clínicamente es lo
más urgente que existe, así que se muestra en rojo.

## Qué hace la UI

1. Crea una sesión con `POST /sesion` y muestra el mensaje de bienvenida.
2. Conserva visualmente el historial del chat.
3. Envía cada respuesta a `POST /mensaje` con el mismo `session_id`.
4. Muestra la pregunta siguiente producida por el backend.
5. En `DEBUG_MODE=true`, muestra en la barra lateral la ficha que Gemma fue
   estructurando turno a turno.
6. Cuando el backend devuelve `resultado` o `derivacion_inmediata`, bloquea más
   mensajes porque la sesión ya fue eliminada del backend.
7. “Nueva consulta” borra la sesión activa y crea otra.

## Decisiones técnicas importantes

- El frontend nunca relee el texto para extraer síntomas.
- El frontend no asigna colores ni ejecuta reglas.
- No se guarda el relato en archivos ni base de datos.
- El backend sigue siendo la única fuente de verdad para la sesión.
- El chequeo `/health` exige el tag exacto del modelo para evitar ejecutar por
  accidente `gemma4:e4b`, que es demasiado pesado para el equipo de prueba.
- El timeout de Gemma es de 180 s y Ollama mantiene el modelo cargado 10 min.
- El parseo traduce los sinónimos coloquiales del enum ("lúcido" → `alerta`),
  porque el modelo los devuelve aunque el enum esté declarado en la tool. Un
  valor que no está en la tabla de sinónimos sigue invalidando el turno: ante
  una posible alucinación se prefiere perder el turno a ensuciar la ficha.

## Tests

```powershell
pytest -q
```

Los tests del backend mockean Gemma y no requieren Ollama.

## Limitación vigente

`app/reglas.py` y `app/recursos.py` siguen siendo módulos STUB. La integración de
Streamlit está completa, pero el resultado final de color no debe presentarse
como clínicamente implementado hasta reemplazar esos módulos y validarlos.
