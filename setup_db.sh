#!/bin/bash
# setup_db.sh — Ejecuta el schema de Bit-Counting en Supabase
# Uso: ./setup_db.sh

set -e

if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

if [ -z "$DATABASE_URL" ]; then
  echo "ERROR: DATABASE_URL no definida. Copia .env.example a .env y configúrala."
  exit 1
fi

echo "Conectando a la base de datos..."
PGPASSWORD="$SUPABASE_PASSWORD" psql "$DATABASE_URL" -f schema.sql

echo ""
echo "Schema aplicado exitosamente."
echo "Tablas creadas:"
PGPASSWORD="$SUPABASE_PASSWORD" psql "$DATABASE_URL" -c "\dt"
