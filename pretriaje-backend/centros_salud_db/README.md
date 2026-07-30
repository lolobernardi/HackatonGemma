# Centros de salud — Paraná y Santa Fe capital

Base de datos **local** con centros de salud, CAPS y hospitales públicos de
**Paraná (Entre Ríos)** y **Santa Fe capital (Santa Fe)**, con su **ubicación**
y sus **especialidades de atención**.

- 86 efectores: 52 en Santa Fe, 34 en Paraná
- 36 especialidades / servicios, 486 relaciones centro–especialidad

Los mismos datos se sirven de dos formas, según lo que necesite cada uno:

| | Cuándo usarla | Arranque |
|---|---|---|
| **PostgreSQL** en `127.0.0.1:5438` | La app del equipo se conecta por TCP con un driver postgres | `.\start_db.ps1` |
| **SQLite** (archivo `centros_salud.db`) | Exploración rápida, scripts, sin levantar nada | `python crear_db.py` |

La fuente de verdad es `datos/centros.json`; las dos bases se generan de ahí.

## PostgreSQL en 127.0.0.1:5438

```powershell
.\start_db.ps1
```

Levanta un PostgreSQL con la base `centros_salud` escuchando **sólo** en
`127.0.0.1:5438`, crea el cluster la primera vez y carga schema + datos si la
base está vacía. `.\start_db.ps1 -Recargar` fuerza recrear todo. Para parar:
`.\stop_db.ps1`.

```
postgresql://postgres@127.0.0.1:5438/centros_salud
```

Usuario `postgres`, sin contraseña (`auth=trust`) — es una base de desarrollo
que no acepta conexiones de afuera de la máquina.

El cluster vive en `postgres/pgdata/` (dentro del proyecto, ~65 MB, ignorado por
git) y **no toca ninguna instalación de PostgreSQL del sistema**, así que no
choca con lo que ya haya en el 5432.

### Requisito

PostgreSQL 18 instalado en un entorno conda aislado, sin permisos de admin:

```bash
conda create -y -n pg-hackaton -c conda-forge postgresql
```

`start_db.ps1` encuentra los binarios solo (ahí o en el `PATH`); si no los
encuentra, te recuerda este comando.

### Consultar desde PowerShell

`psql` no queda en el `PATH` (vive en el entorno conda), así que el atajo es
`consultar.ps1`, que lo encuentra solo y deja la consola en UTF-8:

```powershell
.\consultar.ps1 "SELECT ciudad, count(*) FROM centros GROUP BY ciudad;"
```

```powershell
.\consultar.ps1                                    # sesión interactiva (salir con \q)
.\consultar.ps1 -Archivo postgres\consultas.sql    # correr un archivo .sql
```

Los acentos funcionan en la consulta:

```powershell
.\consultar.ps1 "SELECT centro, direccion FROM vw_centros_especialidades
                 WHERE especialidad = 'Odontología' AND ciudad = 'Paraná';"
```

Con `-Csv` la salida entra al pipeline de PowerShell como objetos:

```powershell
.\consultar.ps1 "SELECT nombre, ciudad, direccion FROM centros;" -Csv |
    ConvertFrom-Csv | Where-Object ciudad -eq 'Paraná' | Format-Table
```

Devuelve exit code `0` si la consulta salió bien y `3` si el SQL falló, así que
se puede usar dentro de otros scripts.

`postgres/consultas.sql` trae consultas de ejemplo ya armadas.

> **Por qué el wrapper y no `psql -c` directo.** PowerShell 5.1 codifica los
> argumentos de los programas nativos en ANSI, nunca en UTF-8, así que
> `psql -c "... WHERE ciudad = 'Paraná'"` falla con
> `invalid byte sequence for encoding "UTF8"` — y `chcp 65001` no lo arregla,
> porque el problema está en `argv`, no en la consola. `consultar.ps1` manda el
> SQL por *stdin*, que sí respeta `$OutputEncoding`, y ahí los acentos pasan
> bien.

Si preferís invocar `psql` a mano:

```powershell
& "$env:USERPROFILE\anaconda3\envs\pg-hackaton\Library\bin\psql.exe" -h 127.0.0.1 -p 5438 -U postgres -d centros_salud
```

### Archivos

- `start_db.ps1` / `stop_db.ps1` — arrancar y parar el servidor
- `consultar.ps1` — consultar desde PowerShell
- `postgres/schema.sql` — DDL en dialecto PostgreSQL
- `postgres/generar_seed.py` — genera `seed.sql` desde `datos/centros.json` (sólo stdlib, no necesita driver)
- `postgres/seed.sql` — INSERTs generados
- `postgres/consultas.sql` — consultas de ejemplo para correr a mano
- `postgres/consultas_api.sql` — **las llamadas que hace la API** (parámetros con nombre para SQLAlchemy, todos los filtros opcionales, orden por cercanía)
- `postgres/pg_comun.ps1` — funciones compartidas por los scripts (ubicar binarios, encoding)

Para cambiar los datos: editar `datos/centros.json`, correr
`python postgres/generar_seed.py` y después `.\start_db.ps1 -Recargar`.

## SQLite

```bash
python crear_db.py
```

Genera `centros_salud.db` desde `schema.sql` + `datos/centros.json`. Es
idempotente: lo vuelve a crear de cero cada vez.

```bash
python consultar.py ciudades
python consultar.py especialidades
python consultar.py buscar pediatria --ciudad Parana
python consultar.py listar --ciudad "Santa Fe" --tipo hospital
python consultar.py centro candioti
```

Las búsquedas ignoran tildes y mayúsculas (`parana` = `Paraná`).

Sin dependencias externas: sólo Python 3.8+ y `sqlite3` de la stdlib.

## Modelo

Igual en las dos bases:

```
centros ──< centro_especialidad >── especialidades
```

`centros`: `nombre`, `tipo`, `dependencia`, `ciudad`, `provincia`, `direccion`,
`barrio`, `telefono`, `horario`, `latitud`, `longitud`, `fuente`.

La relación es muchos-a-muchos porque cada centro atiende varias especialidades.
Para consultar directo conviene la vista `vw_centros_especialidades`, que
devuelve una fila por par centro/especialidad:

```sql
SELECT centro, direccion, ciudad
FROM vw_centros_especialidades
WHERE especialidad = 'Odontología' AND ciudad = 'Paraná';
```

## Datos

Cada fila guarda su `fuente`. Origen por grupo:

| Grupo | Registros | Fuente |
|---|---|---|
| Centros de salud 1er nivel, Santa Fe | 46 | Guía de servicios del primer nivel de atención de la ciudad de Santa Fe, Ministerio de Salud de Santa Fe (act. 01/03/2021) |
| Hospitales, Santa Fe | 6 | Aire de Santa Fe |
| CAPS municipales, Paraná | 6 | Municipalidad de Paraná (parana.gob.ar) |
| Hospitales, Paraná | 6 | hospitalsanmartin.gob.ar, hospitalsanroque.gob.ar, guía Entre Ríos Total |
| Centros de salud provinciales, Paraná | 22 | Guía Entre Ríos Total |

### Limitaciones

- **22 centros de Paraná no tienen especialidades cargadas.** Son los
  provinciales: la dirección y el teléfono están verificados, pero ninguna
  fuente pública consultada publica el detalle de servicios. Están en la base
  con `especialidades` vacío en lugar de datos inventados. Es lo primero a
  completar si se consigue la cartilla provincial.
- `latitud` / `longitud` están en el esquema pero **vacías**: no hay
  geocodificación todavía. La ubicación es la dirección postal.
- La guía de Santa Fe es de **marzo de 2021**; los horarios y la disponibilidad
  de profesionales pueden haber cambiado. Por eso el campo `horario` se cargó
  sólo donde la fuente es actual (los CAPS de Paraná).
- Los nombres de la guía de Santa Fe venían en mayúsculas sin tildes; se
  normalizaron a capitalización y ortografía españolas.

Para editar los datos se toca `datos/centros.json` y se regenera la base que
corresponda (`crear_db.py` para SQLite, `generar_seed.py` +
`start_db.ps1 -Recargar` para PostgreSQL).
