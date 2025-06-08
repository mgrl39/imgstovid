# Generador de Videos para Vinted

Esta aplicación te permite crear videos automáticamente para Vinted y TikTok combinando imágenes y música. Los videos generados tienen una duración de 1 minuto y incluyen transiciones y efectos entre las imágenes.

## Características

- Subida de múltiples imágenes mediante drag & drop o selector de archivos
- Soporte para archivos de música MP3
- Transiciones automáticas entre imágenes
- Efectos visuales aleatorios (zoom, deslizamiento)
- Videos de 1 minuto de duración
- Interfaz web fácil de usar

## Requisitos

- Python 3.7 o superior
- pip (gestor de paquetes de Python)

## Instalación

1. Clona este repositorio o descarga los archivos

2. Crea un entorno virtual (recomendado):
```bash
python -m venv venv
source venv/bin/activate  # En Linux/Mac
# o
venv\Scripts\activate  # En Windows
```

3. Instala las dependencias:
```bash
pip install -r requirements.txt
```

## Uso

1. Inicia la aplicación:
```bash
python app.py
```

2. Abre tu navegador y ve a `http://localhost:5000`

3. En la interfaz web:
   - Arrastra y suelta las imágenes o usa el botón "Selecciona archivos"
   - Sube un archivo de música MP3
   - Haz clic en "Generar Video"
   - El video se descargará automáticamente cuando esté listo

## Formatos soportados

- Imágenes: PNG, JPG, JPEG
- Audio: MP3

## Notas

- Las imágenes se distribuirán uniformemente en el video de 1 minuto
- El audio se cortará automáticamente a 1 minuto si es más largo
- Los efectos se aplican aleatoriamente a cada imagen
- Asegúrate de tener suficiente espacio en disco para los archivos generados

## Solución de problemas

Si encuentras algún error:

1. Asegúrate de que todas las dependencias están instaladas correctamente
2. Verifica que los archivos de imagen y audio tienen formatos soportados
3. Comprueba que tienes permisos de escritura en la carpeta del proyecto
4. Revisa que hay suficiente espacio en disco
