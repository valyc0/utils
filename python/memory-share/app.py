#!/usr/bin/env python3
"""
Memory Share - Collaborative text sharing application
Users can join rooms and share/edit text in real-time with formatting preserved
"""

from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO, join_room, emit
from werkzeug.utils import secure_filename
import os
import sys
from pathlib import Path

app = Flask(__name__)
app.config['SECRET_KEY'] = 'memory-share-secret-key-2023'
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 * 1024  # 20GB max file size
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=20 * 1024 * 1024 * 1024)

# Directory to store room data
ROOMS_DIR = Path('rooms')
ROOMS_DIR.mkdir(exist_ok=True)


def get_room_dir(room_name):
    """Get the directory path for a room"""
    # Sanitize room name for filesystem
    safe_name = "".join(c for c in room_name if c.isalnum() or c in ('-', '_')).rstrip()
    if not safe_name:
        safe_name = "default"
    room_dir = ROOMS_DIR / safe_name
    room_dir.mkdir(exist_ok=True)
    return room_dir


def get_room_file(room_name):
    """Get the chat file path for a room"""
    return get_room_dir(room_name) / "chat.txt"


def load_room_content(room_name):
    """Load content from room chat file"""
    room_file = get_room_file(room_name)
    if room_file.exists():
        with open(room_file, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


def save_room_content(room_name, content):
    """Save content to room chat file"""
    room_file = get_room_file(room_name)
    with open(room_file, 'w', encoding='utf-8') as f:
        f.write(content)


def get_room_files(room_name):
    """Get list of files in a room (excluding chat.txt)"""
    room_dir = get_room_dir(room_name)
    files = []
    for file_path in room_dir.iterdir():
        if file_path.is_file() and file_path.name != 'chat.txt':
            stat = file_path.stat()
            files.append({
                'name': file_path.name,
                'size': stat.st_size,
                'modified': stat.st_mtime
            })
    return sorted(files, key=lambda x: x['modified'], reverse=True)


@app.route('/')
def index():
    """Home page - choose or create a room"""
    return render_template('index.html')


@app.route('/room/<room_name>')
def room(room_name):
    """Room page - collaborative editing"""
    return render_template('room.html', room_name=room_name)


@app.route('/room/<room_name>/upload', methods=['POST'])
def upload_file(room_name):
    """Upload a file to a room"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file:
        filename = secure_filename(file.filename)
        room_dir = get_room_dir(room_name)
        file_path = room_dir / filename
        
        # Save file
        file.save(str(file_path))
        
        # Notify other users
        files_list = get_room_files(room_name)
        socketio.emit('files_updated', {'files': files_list}, room=room_name)
        
        return jsonify({'success': True, 'filename': filename})
    
    return jsonify({'error': 'Upload failed'}), 500


@app.route('/room/<room_name>/files')
def list_files(room_name):
    """Get list of files in a room"""
    files = get_room_files(room_name)
    return jsonify({'files': files})


@app.route('/room/<room_name>/download/<filename>')
def download_file(room_name, filename):
    """Download a file from a room"""
    room_dir = get_room_dir(room_name)
    file_path = room_dir / secure_filename(filename)
    
    if file_path.exists() and file_path.is_file():
        return send_file(str(file_path), as_attachment=True)
    
    return jsonify({'error': 'File not found'}), 404


@app.route('/room/<room_name>/download-chat')
def download_chat(room_name):
    """Download the chat content as a text file"""
    room_file = get_room_file(room_name)
    
    if room_file.exists():
        return send_file(str(room_file), as_attachment=True, download_name=f"{room_name}_chat.txt")
    
    return jsonify({'error': 'Chat not found'}), 404


@app.route('/room/<room_name>/delete/<filename>', methods=['DELETE'])
def delete_file(room_name, filename):
    """Delete a file from a room"""
    room_dir = get_room_dir(room_name)
    file_path = room_dir / secure_filename(filename)
    
    if file_path.exists() and file_path.is_file() and file_path.name != 'chat.txt':
        file_path.unlink()
        
        # Notify other users
        files_list = get_room_files(room_name)
        socketio.emit('files_updated', {'files': files_list}, room=room_name)
        
        return jsonify({'success': True})
    
    return jsonify({'error': 'File not found or cannot be deleted'}), 404


@app.route('/room/<room_name>/delete-room', methods=['DELETE'])
def delete_room(room_name):
    """Delete entire room with all files"""
    import shutil
    room_dir = get_room_dir(room_name)
    
    if room_dir.exists():
        # Remove entire directory and all contents
        shutil.rmtree(room_dir)
        
        # Notify all users in the room
        socketio.emit('room_deleted', {'message': 'Stanza eliminata'}, room=room_name)
        
        return jsonify({'success': True})
    
    return jsonify({'error': 'Room not found'}), 404


@socketio.on('join')
def on_join(data):
    """Handle user joining a room"""
    room_name = data['room']
    join_room(room_name)
    
    # Load and send existing content
    content = load_room_content(room_name)
    emit('load_content', {'content': content})
    
    # Send list of files
    files = get_room_files(room_name)
    emit('files_updated', {'files': files})
    
    # Notify others
    emit('user_joined', {'room': room_name}, room=room_name, skip_sid=request.sid)


@socketio.on('content_update')
def on_content_update(data):
    """Handle content updates from users"""
    room_name = data['room']
    content = data['content']
    
    # Save to file
    save_room_content(room_name, content)
    
    # Broadcast to all users in the room except sender
    emit('content_changed', {'content': content}, room=room_name, skip_sid=request.sid)


@socketio.on('disconnect')
def on_disconnect():
    """Handle user disconnection"""
    pass


if __name__ == '__main__':
    # Get port from command line argument or use default
    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"Invalid port number: {sys.argv[1]}")
            print("Usage: python app.py [port]")
            sys.exit(1)
    
    print(f"Starting Memory Share server on port {port}")
    print(f"Access at: http://localhost:{port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
