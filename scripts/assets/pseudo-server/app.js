const express = require('express');
const http = require('http');
const path = require('path');
const multer = require('multer');
const basicAuth = require('express-basic-auth');
const fs = require('fs');
var app = express();
var port = 4500;

var folder = './files-nodejs/';
if (!fs.existsSync(folder)){
    fs.mkdirSync(folder);
}
// client-0.py prepares and validates server.bin before download checks.

const storage = multer.diskStorage ({
    destination: (req, file, cb) => {
        cb(null, folder)
    },
    
    filename: (req, file, cb) => {
        cb(null, file.originalname)
    }
});
const upload = multer({ storage });

app.get('/', function(req, res) {
    res.send('It works!');
})

app.get('/api/file/:path*', function(req, res) {
    const file = `files-nodejs/${req.params['path']}`;
    res.download(file);
})

app.get('/upload/file', basicAuth({
        users: { 'demo' : '123' }, 
        challenge: true,      
        realm: 'b1234567'
    }), function(req, res) {
    res.send('200 ok')
}                                                                                                                                                                                                   )
  

app.get('/upload/video', basicAuth({
        users: { 'demo' : '123' }, 
        challenge: true,      
        realm: 'b1234567'
    }), function(req, res) {
    res.send('200 ok')
}                                                                                                                                                                                                   )
       
app.post('/api/file', basicAuth({
        users: { 'demo': '123' },
        challenge: true,
        realm: 'b12345678'
    }), upload.single('upfile'), function(req, res) {
    const file = req.file;
    if (!file) {
        res.status(500).send();
    }
    res.send('200 OK');
})

// Configure the HTTP server before it accepts connections. Express app settings
// do not control socket timeouts. Allow time for the judge to compare large files.
const server = http.createServer(app);
server.keepAliveTimeout = 120 * 1000;
server.headersTimeout = 125 * 1000;
server.requestTimeout = 300 * 1000;
server.timeout = 0;

server.listen(port, function() {
  console.log('listen on ' + port);
})
