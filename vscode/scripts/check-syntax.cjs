// Optional offline syntax check; npm run check performs the full type check.
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.argv[2] || 'typescript');
const root = path.resolve(__dirname, '..');
for (const file of ['src/extension.ts', 'src/view.js']) {
  const result = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    fileName: file, compilerOptions: { target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS, strict: true, allowJs: true }, reportDiagnostics: true
  });
  const errors = (result.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
  for (const diagnostic of errors) {
    console.error(ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'));
  }
  if (errors.length) { process.exitCode = 1; }
  else { console.log(`${file}: syntax OK`); }
}
