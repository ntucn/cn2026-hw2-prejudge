from flask import Flask, request, send_from_directory
from flask_httpauth import HTTPBasicAuth
import os

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'files-flask'
# T31: the limit covers the whole multipart body; allow a 200 MiB file plus
# 1 MiB of form overhead so a legal file is never refused by the judge side.
app.config['MAX_CONTENT_LENGTH'] = 201 * 1024 * 1024
auth = HTTPBasicAuth()

users = {
    "demo": "123"
}

@auth.verify_password
def verify_password(username, password):
    if username in users and users.get(username) == password:
        return username
    
@app.after_request
def close_connection(response):
    # This peer intentionally exercises clients that reconnect after each response.
    response.headers['Connection'] = 'close'
    return response

@app.route('/', methods=['GET'])
def test():
    return "It works!"

@app.route('/api/file/<path:filepath>', methods=['GET'])
def download(filepath):
    return send_from_directory('files-flask', filepath)

@app.route('/upload/file', methods=['GET'])
@auth.login_required
def uploadFile():
    return "200 OK", 200

@app.route('/upload/video', methods=['GET'])
@auth.login_required
def uploadVideo():
    return "200 OK", 200


@app.route('/api/file', methods=['POST'])
@auth.login_required
def upload():
    file = request.files['upfile']
    if file:
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        return "200 OK"

if __name__ == '__main__':
    if not os.path.exists('files-flask'):
        os.makedirs('files-flask')

    # client-0.py prepares and validates server.bin before download checks.
    app.run(debug=False, use_reloader=False, load_dotenv=False, port=2024, host="0.0.0.0")
