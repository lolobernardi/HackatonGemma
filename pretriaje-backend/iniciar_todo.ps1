<#
    ===========================================================================
     Pre-triaje conversacional — arranque completo del sistema
    ===========================================================================

    Levanta los tres servicios del proyecto, cada uno en su propia ventana de
    PowerShell, en el orden en que se necesitan:

        1. PostgreSQL con la base de centros de salud   127.0.0.1:5438
        2. Backend FastAPI (orquestador + Gemma)        127.0.0.1:8000
        3. Frontend Streamlit                           127.0.0.1:8501

    Antes de cada paso espera a que el anterior responda de verdad, no un
    `sleep` fijo: el backend consulta la base al arrancar y Streamlit consulta
    el backend, así que arrancarlos a ciegas produce fallas intermitentes que
    parecen del código y son de tiempos.

    ---------------------------------------------------------------------------
     Portabilidad
    ---------------------------------------------------------------------------

    No hay una sola ruta absoluta en este archivo. Todo cuelga de $PSScriptRoot,
    que es la carpeta donde está ESTE script, así que el proyecto se puede
    clonar o copiar a cualquier lado y a cualquier usuario, y funciona igual.

    La única dependencia externa que se busca por el sistema es PostgreSQL, y
    de eso se encarga `centros_salud_db\postgres\pg_comun.ps1`, que lo resuelve
    por PATH o preguntándole a conda dónde vive.

    ---------------------------------------------------------------------------
     Uso
    ---------------------------------------------------------------------------

        .\iniciar_todo.ps1                 arranque normal
        .\iniciar_todo.ps1 -Preparar       fuerza reinstalar el entorno de Python
        .\iniciar_todo.ps1 -SinNavegador   no abre el navegador al terminar

    Si PowerShell se queja de la firma del script, usá `iniciar_todo.cmd`, que
    hace lo mismo salteando la política de ejecución.
#>

[CmdletBinding()]
param(
    # Reinstala dependencias aunque el entorno virtual ya exista.
    [switch]$Preparar,
    # No abre el navegador al final.
    [switch]$SinNavegador
)

$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------- #
#  Rutas: TODAS relativas a este archivo
# --------------------------------------------------------------------------- #

$RaizProyecto = $PSScriptRoot
$DirBase      = Join-Path $RaizProyecto 'centros_salud_db'
$DirApp       = Join-Path $RaizProyecto 'pretriaje_gemma_streamlit_integrado'

$ScriptBase     = Join-Path $DirBase 'start_db.ps1'
$ScriptBackend  = Join-Path $DirApp  'iniciar_backend.ps1'
$ScriptFrontend = Join-Path $DirApp  'iniciar_frontend.ps1'
$ScriptPreparar = Join-Path $DirApp  'preparar_entorno.ps1'
$PythonVenv     = Join-Path $DirApp  '.venv\Scripts\python.exe'
$ArchivoEnv     = Join-Path $DirApp  '.env'

$PuertoBase     = 5438
$PuertoBackend  = 8000
$PuertoFrontend = 8501

# --------------------------------------------------------------------------- #
#  Utilidades
# --------------------------------------------------------------------------- #

function Write-Titulo {
    param([string]$Texto)
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
    Write-Host "  $Texto" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
}

function Write-Paso {
    param([string]$Texto)
    Write-Host ''
    Write-Host ">> $Texto" -ForegroundColor White
}

function Write-Ok    { param([string]$T) Write-Host "   [OK]    $T" -ForegroundColor Green }
function Write-Aviso { param([string]$T) Write-Host "   [AVISO] $T" -ForegroundColor Yellow }
function Write-Error2 { param([string]$T) Write-Host "   [ERROR] $T" -ForegroundColor Red }

function Test-Puerto {
    <#
        ¿Hay algo escuchando en ese puerto de localhost?

        Se usa TcpClient y no Test-NetConnection porque este chequeo corre en
        un bucle de espera y Test-NetConnection tarda ~1s por llamada.
    #>
    param([int]$Puerto)
    $cliente = New-Object System.Net.Sockets.TcpClient
    try {
        $intento = $cliente.BeginConnect('127.0.0.1', $Puerto, $null, $null)
        $conecto = $intento.AsyncWaitHandle.WaitOne(400)
        if ($conecto) { $cliente.EndConnect($intento) }
        return $conecto
    } catch {
        return $false
    } finally {
        $cliente.Close()
    }
}

function Wait-Puerto {
    <# Espera hasta que el puerto responda. Devuelve $true si lo logró. #>
    param(
        [int]$Puerto,
        [string]$Que,
        [int]$SegundosMax = 120
    )
    $limite = (Get-Date).AddSeconds($SegundosMax)
    Write-Host "   esperando a $Que (puerto $Puerto)" -NoNewline
    while ((Get-Date) -lt $limite) {
        if (Test-Puerto -Puerto $Puerto) {
            Write-Host ''
            return $true
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Milliseconds 700
    }
    Write-Host ''
    return $false
}

function Get-Json {
    <# GET que devuelve el objeto ya parseado, o $null si falla. #>
    param([string]$Url, [int]$TimeoutSeg = 8)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeg
        return ($r.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Wait-Health {
    <#
        Espera a que /health del backend conteste.

        No alcanza con que el puerto abra: uvicorn escucha antes de terminar de
        levantar la app, y Streamlit consulta /health apenas arranca.
    #>
    param([int]$SegundosMax = 120)
    $limite = (Get-Date).AddSeconds($SegundosMax)
    Write-Host '   esperando a que el backend responda /health' -NoNewline
    while ((Get-Date) -lt $limite) {
        $salud = Get-Json "http://127.0.0.1:$PuertoBackend/health" 3
        if ($salud) {
            Write-Host ''
            return $salud
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Milliseconds 900
    }
    Write-Host ''
    return $null
}

function Start-Ventana {
    <#
        Abre una ventana de PowerShell nueva, con título propio, y corre ahí el
        script indicado.

        -NoExit deja la ventana abierta al terminar: si el servicio se cae, el
        error queda a la vista en lugar de desaparecer con la ventana.
        -ExecutionPolicy Bypass evita el "is not digitally signed" en equipos
        donde la política está en Restricted.
    #>
    param(
        [string]$Titulo,
        [string]$Script,
        [string]$Directorio
    )
    $comando = "`$host.UI.RawUI.WindowTitle = '$Titulo'; Set-Location '$Directorio'; & '$Script'"
    Start-Process -FilePath 'powershell' -ArgumentList @(
        '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $comando
    ) | Out-Null
}

function Read-DotEnv {
    <# Lee un .env a hashtable. Ignora comentarios y líneas vacías. #>
    param([string]$Ruta)
    $valores = @{}
    if (-not (Test-Path $Ruta)) { return $valores }
    foreach ($linea in Get-Content $Ruta -Encoding UTF8) {
        $t = $linea.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or ($t -notmatch '=')) { continue }
        $partes = $t -split '=', 2
        $valores[$partes[0].Trim()] = $partes[1].Trim().Trim('"').Trim("'")
    }
    return $valores
}

function Get-Config {
    <# Valor del .env, o el default si no está. #>
    param([hashtable]$Env, [string]$Clave, [string]$Default)
    if ($Env.ContainsKey($Clave) -and $Env[$Clave]) { return $Env[$Clave] }
    return $Default
}

# --------------------------------------------------------------------------- #
#  0. Verificación de la estructura del proyecto
# --------------------------------------------------------------------------- #

Write-Titulo 'PRE-TRIAJE CONVERSACIONAL — ARRANQUE'
Write-Host "  Proyecto: $RaizProyecto" -ForegroundColor DarkGray

foreach ($requerido in @($DirBase, $DirApp, $ScriptBase, $ScriptBackend, $ScriptFrontend)) {
    if (-not (Test-Path $requerido)) {
        Write-Error2 "Falta una parte del proyecto: $requerido"
        Write-Host ''
        Write-Host '   Este script tiene que vivir en la carpeta que contiene' -ForegroundColor Yellow
        Write-Host '   centros_salud_db\ y pretriaje_gemma_streamlit_integrado\.' -ForegroundColor Yellow
        exit 1
    }
}
Write-Ok 'Estructura del proyecto correcta'

# --------------------------------------------------------------------------- #
#  1. Gemma: qué modelo se usa y cómo está integrado
# --------------------------------------------------------------------------- #
#  Se muestra antes de arrancar nada porque es la dependencia que más suele
#  fallar: el tag del modelo cambia de máquina en máquina y /health lo compara
#  literalmente.

$configEnv = Read-DotEnv $ArchivoEnv

$modelo     = Get-Config $configEnv 'MODELO'          'gemma4:12b'
$ollamaUrl  = Get-Config $configEnv 'OLLAMA_URL'      'http://localhost:11434'
$think      = Get-Config $configEnv 'GEMMA_THINK'     'false'
$numCtx     = Get-Config $configEnv 'NUM_CTX'         '4096'
$timeoutS   = Get-Config $configEnv 'TIMEOUT_GEMMA_S' '180'

Write-Titulo 'GEMMA — CÓMO ESTÁ IMPLEMENTADO'

Write-Host ''
Write-Host '  El modelo NO decide la urgencia. Su único trabajo es entender el' -ForegroundColor White
Write-Host '  relato en lenguaje natural y volcarlo a una ficha estructurada.' -ForegroundColor White
Write-Host ''
Write-Host '     Persona escribe en castellano' -ForegroundColor DarkGray
Write-Host '              |' -ForegroundColor DarkGray
Write-Host '              v' -ForegroundColor DarkGray
Write-Host '     Gemma vía Ollama  ->  tool call `actualizar_ficha`' -ForegroundColor Cyan
Write-Host '              |              (app/gemma.py, app/prompt.py)' -ForegroundColor DarkGray
Write-Host '              v' -ForegroundColor DarkGray
Write-Host '     Ficha clínica estructurada (app/schema.py)' -ForegroundColor DarkGray
Write-Host '              |' -ForegroundColor DarkGray
Write-Host '              v' -ForegroundColor DarkGray
Write-Host '     Motor de reglas determinístico  ->  color de triaje' -ForegroundColor Green
Write-Host '              (app/reglas.py + app/ruleset.yaml)' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Gemma tiene PROHIBIDO diagnosticar, nombrar enfermedades y opinar' -ForegroundColor White
Write-Host '  sobre urgencia. Eso está en el system prompt y es lo que hace que' -ForegroundColor White
Write-Host '  la clasificación sea auditable: si un color está mal, está mal en' -ForegroundColor White
Write-Host '  el ruleset, no en el modelo.' -ForegroundColor White
Write-Host ''
Write-Host '  Configuración activa (pretriaje_gemma_streamlit_integrado\.env):' -ForegroundColor White
Write-Host "     modelo            $modelo" -ForegroundColor Yellow
Write-Host "     endpoint Ollama   $ollamaUrl  (local, sin salida a internet)"
Write-Host "     thinking          $think" -NoNewline
if ($think -eq 'false') {
    Write-Host '   <- necesario: con thinking activo no emite la tool call' -ForegroundColor DarkGray
} else {
    Write-Host ''
    Write-Aviso 'Con GEMMA_THINK=true el modelo razona en voz alta y suele no emitir la tool call.'
}
Write-Host "     contexto          $numCtx tokens"
Write-Host "     timeout           $timeoutS s"

# --- ¿Está Ollama corriendo y el modelo bajado? --------------------------- #

Write-Paso 'Verificando Ollama'

$tags = Get-Json "$ollamaUrl/api/tags" 6
if (-not $tags) {
    Write-Error2 "Ollama no responde en $ollamaUrl"
    Write-Host ''
    Write-Host '   Abrí la aplicación de Ollama, o corré en otra terminal:' -ForegroundColor Yellow
    Write-Host '       ollama serve' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '   Sin Ollama el backend arranca igual, pero cada consulta va a' -ForegroundColor DarkGray
    Write-Host '   fallar con el mensaje de error seguro.' -ForegroundColor DarkGray
    exit 1
}

$disponibles = @($tags.models | ForEach-Object { $_.name })
Write-Ok "Ollama responde ($($disponibles.Count) modelo(s) descargado(s))"

if ($disponibles -contains $modelo) {
    Write-Ok "El modelo '$modelo' está descargado"
} else {
    Write-Error2 "El modelo '$modelo' NO está en esta computadora."
    Write-Host ''
    Write-Host '   Modelos disponibles acá:' -ForegroundColor Yellow
    $disponibles | ForEach-Object { Write-Host "       $_" -ForegroundColor Yellow }
    Write-Host ''
    Write-Host '   Ajustá MODELO en pretriaje_gemma_streamlit_integrado\.env con' -ForegroundColor Yellow
    Write-Host '   uno de esos, o descargá el que falta:' -ForegroundColor Yellow
    Write-Host "       ollama pull $modelo" -ForegroundColor Yellow
    Write-Host ''
    Write-Host '   /health compara el tag literalmente, por eso tiene que coincidir exacto.' -ForegroundColor DarkGray
    exit 1
}

# --------------------------------------------------------------------------- #
#  2. Entorno de Python
# --------------------------------------------------------------------------- #
#  Un .venv guarda la ruta absoluta del intérprete que lo creó, así que el que
#  viaja dentro de un zip o de OneDrive queda muerto en la máquina destino. Se
#  comprueba que el intérprete realmente ejecute, no que el archivo exista.

Write-Paso 'Verificando el entorno de Python'

$venvSirve = $false
if (Test-Path $PythonVenv) {
    & $PythonVenv -c "import fastapi, uvicorn, streamlit, sqlalchemy" 2>$null | Out-Null
    $venvSirve = ($LASTEXITCODE -eq 0)
}

if ($Preparar -or -not $venvSirve) {
    if (-not $venvSirve -and (Test-Path $PythonVenv)) {
        Write-Aviso 'El .venv no sirve en esta computadora (o le faltan dependencias).'
    } elseif (-not (Test-Path $PythonVenv)) {
        Write-Aviso 'No hay entorno virtual todavía.'
    }
    Write-Host '   Preparando el entorno, esto puede tardar unos minutos...' -ForegroundColor DarkGray
    Write-Host ''
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScriptPreparar
    if ($LASTEXITCODE -ne 0) {
        Write-Error2 'Falló la preparación del entorno.'
        exit 1
    }
    Write-Ok 'Entorno preparado'
} else {
    Write-Ok 'Entorno virtual listo'
}

# --------------------------------------------------------------------------- #
#  3. Base de datos de centros de salud
# --------------------------------------------------------------------------- #

Write-Titulo 'VENTANA 1 de 3 — BASE DE CENTROS DE SALUD'

if (Test-Puerto -Puerto $PuertoBase) {
    Write-Ok "Ya había algo escuchando en $PuertoBase, se reutiliza"
} else {
    Write-Paso "Abriendo ventana y levantando PostgreSQL en 127.0.0.1:$PuertoBase"
    Start-Ventana -Titulo '1/3 - Base de centros (PostgreSQL 5438)' `
                  -Script $ScriptBase -Directorio $DirBase

    if (-not (Wait-Puerto -Puerto $PuertoBase -Que 'PostgreSQL' -SegundosMax 120)) {
        Write-Error2 'La base no levantó. Mirá la ventana "1/3" para ver por qué.'
        Write-Host '   Causa habitual: falta PostgreSQL. Se instala sin permisos de admin con:' -ForegroundColor Yellow
        Write-Host '       conda create -y -n pg-hackaton -c conda-forge postgresql' -ForegroundColor Yellow
        exit 1
    }
    Write-Ok 'Base de centros escuchando'
}

# --------------------------------------------------------------------------- #
#  4. Backend
# --------------------------------------------------------------------------- #

Write-Titulo 'VENTANA 2 de 3 — BACKEND FastAPI + GEMMA'

if (Test-Puerto -Puerto $PuertoBackend) {
    Write-Aviso "El puerto $PuertoBackend ya está ocupado; se reutiliza lo que haya."
} else {
    Write-Paso "Abriendo ventana y levantando el backend en 127.0.0.1:$PuertoBackend"
    Start-Ventana -Titulo '2/3 - Backend FastAPI + Gemma (8000)' `
                  -Script $ScriptBackend -Directorio $DirApp
}

$salud = Wait-Health -SegundosMax 120
if (-not $salud) {
    Write-Error2 'El backend no respondió /health. Mirá la ventana "2/3".'
    exit 1
}

Write-Ok "Backend arriba — modelo en uso: $($salud.modelo)"
if ($salud.centros_db) {
    Write-Ok 'El backend ve la base de centros'
} else {
    Write-Aviso 'El backend no ve la base de centros. El triaje funciona igual,'
    Write-Host '           pero no va a poder sugerir a qué centro ir.' -ForegroundColor Yellow
}

# --------------------------------------------------------------------------- #
#  5. Frontend
# --------------------------------------------------------------------------- #

Write-Titulo 'VENTANA 3 de 3 — FRONTEND Streamlit'

if (Test-Puerto -Puerto $PuertoFrontend) {
    Write-Aviso "El puerto $PuertoFrontend ya está ocupado; se reutiliza lo que haya."
} else {
    Write-Paso "Abriendo ventana y levantando Streamlit en 127.0.0.1:$PuertoFrontend"
    Start-Ventana -Titulo '3/3 - Frontend Streamlit (8501)' `
                  -Script $ScriptFrontend -Directorio $DirApp

    if (-not (Wait-Puerto -Puerto $PuertoFrontend -Que 'Streamlit' -SegundosMax 120)) {
        Write-Error2 'Streamlit no levantó. Mirá la ventana "3/3".'
        exit 1
    }
}
Write-Ok 'Frontend escuchando'

# --------------------------------------------------------------------------- #
#  6. Resumen
# --------------------------------------------------------------------------- #

Write-Titulo 'TODO ARRIBA'

Write-Host ''
Write-Host '  Servicio                        Dirección                  Ventana' -ForegroundColor White
Write-Host '  ------------------------------  -------------------------  -------'
Write-Host "  Base de centros (PostgreSQL)    127.0.0.1:$PuertoBase             1/3"
Write-Host "  Backend FastAPI + Gemma         http://127.0.0.1:$PuertoBackend      2/3"
Write-Host "  Frontend Streamlit              http://localhost:$PuertoFrontend      3/3"
Write-Host ''
Write-Host "  Modelo Gemma en uso: $($salud.modelo)" -ForegroundColor Yellow
Write-Host "  Ubicación simulada:  $($salud.ciudad_paciente)"
Write-Host ''
Write-Host '  Para parar todo:' -ForegroundColor White
Write-Host '     Ctrl+C en las ventanas 2/3 y 3/3, y después:'
Write-Host '     cd centros_salud_db; .\stop_db.ps1' -ForegroundColor DarkGray
Write-Host ''

if (-not $SinNavegador) {
    Write-Host "  Abriendo http://localhost:$PuertoFrontend ..." -ForegroundColor DarkGray
    Start-Process "http://localhost:$PuertoFrontend"
}

Write-Host ''
