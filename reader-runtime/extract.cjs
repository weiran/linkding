const { JSDOM } = require('jsdom');
const Readability = require('../bookmarks/static/vendor/Readability.js');
let input = ''; process.stdin.setEncoding('utf8');
process.stdin.on('data', s => { input += s; if (input.length > 6 * 1024 * 1024) process.exit(2); });
process.stdin.on('end', () => {
  const {html, url} = JSON.parse(input);
  // No scripts, external resources or publisher execution are enabled.
  const dom = new JSDOM(html, {url});
  const article = new Readability(dom.window.document).parse();
  if (!article || article.textContent.trim().length < 100) process.exit(3);
  process.stdout.write(JSON.stringify({title:article.title,html:article.content}));
});
