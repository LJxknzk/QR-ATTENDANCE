const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const www = path.resolve(__dirname, 'www');

function copyRecursive(src, dest){
  if(!fs.existsSync(src)) return;
  const stat = fs.statSync(src);
  if(stat.isDirectory()){
    if(!fs.existsSync(dest)) fs.mkdirSync(dest, { recursive: true });
    for(const f of fs.readdirSync(src)){
      copyRecursive(path.join(src, f), path.join(dest, f));
    }
  } else {
    fs.copyFileSync(src, dest);
  }
}

// Ensure www folder
if(!fs.existsSync(www)) fs.mkdirSync(www, { recursive: true });

// Files to copy from project root into www (adjust as needed)
const files = ['index.html','teacher.html','student.html','admin.html','accountcreate.html'];
for(const f of files){
  const src = path.join(root, f);
  const dest = path.join(www, f);
  copyRecursive(src, dest);
}

// Copy CSS and JS folders
copyRecursive(path.join(root, 'CSS'), path.join(www, 'CSS'));
copyRecursive(path.join(root, 'JS'), path.join(www, 'JS'));

console.log('Web assets copied to mobile/www');
