# Boletines Cudós

Recoge cada mañana las publicaciones del BOE, BDNS, DOGC, BOP de Lleida, Ajuntament de Lleida y los Consells Comarcals de la Noguera y del Segrià, y las guarda en `datos/AAAA-MM-DD.json` y `datos/ultimo.json`.

## Puesta en marcha

1. Crea un repositorio en GitHub llamado `cudos-boletines`. Público, para que la tarea de Claude pueda leer los datos sin claves.
2. Sube estos archivos manteniendo las carpetas:
   - `recoger.py`
   - `requirements.txt`
   - `.github/workflows/recoger.yml`
3. Ve a la pestaña **Actions**, abre "Recoger boletines" y pulsa **Run workflow**.
4. Cuando termine (unos 3 minutos), abre `datos/ultimo.json` y revisa el bloque `fuentes`: indica qué fuentes dieron `OK`, `VACIO` o `ERROR` y el motivo.

Desde ese momento se ejecuta solo de lunes a sábado a las 07:00 (06:00 en invierno).

## Probar en tu ordenador

```
pip install -r requirements.txt
python -m playwright install chromium
python recoger.py
FECHA=2026-10-21 python recoger.py   # para un día concreto
```

## Ajustes

Las webs sin API se leen recogiendo los enlaces de la página. Si una fuente trae demasiados enlaces irrelevantes o ninguno, se ajusta su entrada en la lista `PAGINAS` de `recoger.py` (URL, `filtro` o `patron_enlace`).
