-- Base de datos local de centros de salud de Paraná (Entre Ríos)
-- y Santa Fe capital (Santa Fe).

PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS vw_centros_especialidades;
DROP TABLE IF EXISTS centro_especialidad;
DROP TABLE IF EXISTS especialidades;
DROP TABLE IF EXISTS centros;

CREATE TABLE centros (
    id          INTEGER PRIMARY KEY,
    nombre      TEXT NOT NULL,
    tipo        TEXT NOT NULL,   -- centro de salud | CAPS | hospital | CIC | SAMCO | ...
    dependencia TEXT NOT NULL,   -- provincial | municipal
    ciudad      TEXT NOT NULL,
    provincia   TEXT NOT NULL,
    direccion   TEXT NOT NULL,
    barrio      TEXT,            -- barrio o distrito municipal
    telefono    TEXT,
    horario     TEXT,
    latitud     REAL,            -- sin geocodificar todavía
    longitud    REAL,
    fuente      TEXT NOT NULL,
    UNIQUE (nombre, ciudad)
);

CREATE TABLE especialidades (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE centro_especialidad (
    centro_id       INTEGER NOT NULL REFERENCES centros(id)        ON DELETE CASCADE,
    especialidad_id INTEGER NOT NULL REFERENCES especialidades(id) ON DELETE CASCADE,
    PRIMARY KEY (centro_id, especialidad_id)
);

CREATE INDEX idx_centros_ciudad     ON centros (ciudad);
CREATE INDEX idx_centros_tipo       ON centros (tipo);
CREATE INDEX idx_ce_especialidad    ON centro_especialidad (especialidad_id);

-- Una fila por par centro/especialidad: es la vista que conviene consultar.
CREATE VIEW vw_centros_especialidades AS
SELECT c.id            AS centro_id,
       c.nombre        AS centro,
       c.tipo,
       c.dependencia,
       c.ciudad,
       c.provincia,
       c.direccion,
       c.barrio,
       c.telefono,
       c.horario,
       e.nombre        AS especialidad
FROM centros c
LEFT JOIN centro_especialidad ce ON ce.centro_id = c.id
LEFT JOIN especialidades e       ON e.id = ce.especialidad_id;
