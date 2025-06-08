import os
from datetime import datetime
from flask import Flask, request, render_template, jsonify, send_file, Response
from werkzeug.utils import secure_filename
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, ColorClip
from PIL import Image
import random
import uuid
import json

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max-limit

# Configuración
class Config:
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}
    DEFAULT_IMAGE_DURATION = 3
    OUTPUT_FORMAT = 'mp4'
    FPS = 30
    TRANSITION_DURATION = 0.5
    MAX_VIDEO_DURATION = 300  # 5 minutos máximo
    VIDEO_SIZE = (1080, 1920)  # Tamaño vertical para TikTok/Reels

# Utilidades
class VideoUtils:
    @staticmethod
    def create_directories():
        for folder in ['uploads', 'uploads/images', 'uploads/music', 'uploads/output']:
            os.makedirs(folder, exist_ok=True)

    @staticmethod
    def allowed_file(filename, allowed_extensions):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

    @staticmethod
    def generate_unique_filename(original_filename):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        ext = original_filename.rsplit('.', 1)[1].lower()
        return f"{timestamp}_{unique_id}.{ext}"

    @staticmethod
    def clean_old_files(directory, max_files=10):
        """Mantiene solo los últimos max_files archivos en el directorio"""
        files = sorted(
            [os.path.join(directory, f) for f in os.listdir(directory)],
            key=os.path.getctime
        )
        for f in files[:-max_files]:  # Mantener solo los últimos max_files
            try:
                os.remove(f)
            except:
                pass

    @staticmethod
    def resize_image(image_path):
        with Image.open(image_path) as img:
            # Redimensionar manteniendo la relación de aspecto
            img = img.convert('RGB')
            ratio = min(Config.VIDEO_SIZE[0]/img.size[0], Config.VIDEO_SIZE[1]/img.size[1])
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.LANCZOS)
            
            # Crear un fondo negro del tamaño del video
            background = Image.new('RGB', Config.VIDEO_SIZE, (0, 0, 0))
            
            # Centrar la imagen en el fondo
            offset = ((Config.VIDEO_SIZE[0] - new_size[0]) // 2,
                     (Config.VIDEO_SIZE[1] - new_size[1]) // 2)
            background.paste(img, offset)
            
            # Guardar la imagen procesada
            processed_path = image_path.replace('.', '_processed.')
            background.save(processed_path, 'JPEG', quality=95)
            return processed_path

class Transitions:
    @staticmethod
    def fade():
        return lambda t: 1 if t > 0.5 else 2*t

    @staticmethod
    def slide_left(clip, duration):
        return clip.set_position(lambda t: (Config.VIDEO_SIZE[0]*(0.5-t/duration), 'center'))

    @staticmethod
    def slide_right(clip, duration):
        return clip.set_position(lambda t: (-Config.VIDEO_SIZE[0]*(0.5-t/duration), 'center'))

    @staticmethod
    def zoom_in(clip):
        return clip.resize(lambda t: 1 + 0.3*t)

    @staticmethod
    def zoom_out(clip):
        return clip.resize(lambda t: 1.3 - 0.3*t)

    @staticmethod
    def rotate(clip):
        return clip.rotate(lambda t: 360*t)

class VideoGenerator:
    def __init__(self, image_files, audio_file, options=None):
        self.image_files = image_files
        self.audio_file = audio_file
        self.options = options or {}
        self.transitions = {
            'fade': Transitions.fade,
            'slide_left': Transitions.slide_left,
            'slide_right': Transitions.slide_right,
            'zoom_in': Transitions.zoom_in,
            'zoom_out': Transitions.zoom_out,
            'rotate': Transitions.rotate
        }

    def create_transition(self, clip1, clip2, transition_type):
        duration = Config.TRANSITION_DURATION
        if transition_type == 'fade':
            clip2 = clip2.set_start(clip1.end - duration)
            clip2 = clip2.crossfadein(duration)
            return [clip1, clip2]
        elif transition_type in ['slide_left', 'slide_right']:
            clip2 = clip2.set_start(clip1.end - duration)
            clip1 = getattr(Transitions, transition_type)(clip1, duration)
            clip2 = getattr(Transitions, transition_type)(clip2, duration)
            return [clip1, clip2]
        else:
            clip2 = clip2.set_start(clip1.end)
            return [getattr(Transitions, transition_type)(clip1), clip2]

    def generate(self):
        clips = []
        progress_callback = self.options.get('progress_callback', lambda x: None)
        
        for i, img in enumerate(self.image_files):
            progress = (i / len(self.image_files)) * 100
            progress_callback(progress)
            
            # Procesar y redimensionar la imagen
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'images', img)
            processed_path = VideoUtils.resize_image(img_path)
            
            duration = self.options.get('durations', {}).get(img, Config.DEFAULT_IMAGE_DURATION)
            clip = ImageClip(processed_path)
            clip = clip.set_duration(duration)
            
            if i > 0:
                transition = self.options.get('transitions', {}).get(str(i), 'fade')
                clips.extend(self.create_transition(clips[-1], clip, transition))
            else:
                clips.append(clip)
            
            # Limpiar imagen procesada
            try:
                os.remove(processed_path)
            except:
                pass

        final_clip = concatenate_videoclips(clips, method='compose')
        
        # Añadir audio
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'music', self.audio_file)
        audio = AudioFileClip(audio_path)
        total_duration = final_clip.duration
        audio = audio.set_duration(total_duration)
        audio = audio.volumex(self.options.get('audio_volume', 1.0))
        
        final_clip = final_clip.set_audio(audio)
        
        output_filename = VideoUtils.generate_unique_filename(f"video.{Config.OUTPUT_FORMAT}")
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'output', output_filename)
        
        final_clip.write_videofile(output_path, fps=Config.FPS, codec='libx264', audio_codec='aac')
        progress_callback(100)
        
        return output_filename

# Crear directorios necesarios al inicio
VideoUtils.create_directories()

@app.route('/')
def index():
    # Obtener lista de videos generados
    videos = sorted(
        [f for f in os.listdir(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))],
        key=lambda x: os.path.getctime(os.path.join(app.config['UPLOAD_FOLDER'], 'output', x)),
        reverse=True
    )[:5]  # Mostrar solo los últimos 5 videos
    return render_template('index.html', videos=videos)

@app.route('/upload_images', methods=['POST'])
def upload_images():
    if 'images[]' not in request.files:
        return jsonify({'error': 'No se encontraron imágenes'}), 400
    
    files = request.files.getlist('images[]')
    filenames = []
    
    for file in files:
        if file and VideoUtils.allowed_file(file.filename, Config.ALLOWED_IMAGE_EXTENSIONS):
            filename = VideoUtils.generate_unique_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename))
            filenames.append(filename)
    
    # Limpiar archivos antiguos
    VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'images'))
    return jsonify({'filenames': filenames})

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    if 'audio' not in request.files:
        return jsonify({'error': 'No se encontró archivo de audio'}), 400
    
    file = request.files['audio']
    if file and VideoUtils.allowed_file(file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
        filename = VideoUtils.generate_unique_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'music', filename))
        # Limpiar archivos antiguos
        VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'music'))
        return jsonify({'filename': filename})
    
    return jsonify({'error': 'Formato de archivo no permitido'}), 400

@app.route('/generate_video', methods=['POST'])
def generate_video():
    data = request.json
    image_files = data.get('images', [])
    audio_file = data.get('audio')
    options = data.get('options', {})
    
    if not image_files or not audio_file:
        return jsonify({'error': 'Faltan imágenes o audio'}), 400

    try:
        def progress_callback(progress):
            print(f"Progress: {progress}%")
        
        options['progress_callback'] = progress_callback
        generator = VideoGenerator(image_files, audio_file, options)
        output_filename = generator.generate()
        
        # Limpiar videos antiguos
        VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))
        
        return jsonify({
            'success': True,
            'video_path': output_filename,
            'duration': sum(options.get('durations', {}).get(img, Config.DEFAULT_IMAGE_DURATION) 
                          for img in image_files)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(
        os.path.join(app.config['UPLOAD_FOLDER'], 'output', filename),
        as_attachment=True
    )

@app.route('/preview/<filename>')
def preview_file(filename):
    return send_file(
        os.path.join(app.config['UPLOAD_FOLDER'], 'output', filename)
    )

@app.route('/videos')
def list_videos():
    videos = sorted(
        [f for f in os.listdir(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))],
        key=lambda x: os.path.getctime(os.path.join(app.config['UPLOAD_FOLDER'], 'output', x)),
        reverse=True
    )
    return jsonify({'videos': videos})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
