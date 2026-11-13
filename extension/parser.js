// Parses the tool-call blocks the model writes.
//
// The format avoids JSON on purpose: asking a model to escape source code
// inside a JSON string is the most common cause of broken edits.
//
//   name: replace_in_file
//   path: src/app.js
//   =====SEARCH=====
//   old code
//   =====REPLACE=====
//   new code
//   =====END=====
//
// Skill tools use header lines and an optional =====BODY===== ... =====END=====.
// Header lines are "key: value". write_file takes everything after
// =====CONTENT===== verbatim. replace_in_file takes one or more
// SEARCH/REPLACE pairs. Both end with =====END=====.

(function (root) {
  const KNOWN = ['list_dir', 'read_file', 'write_file', 'replace_in_file', 'delete_file', 'search', 'run_command'];
  const MARK = /^=====([A-Z_]+)=====\s*$/;

  // `custom` = names of tools added by skills (the bridge tells the extension which exist).
  function parseBlock(raw, custom) {
    const customNames = Array.isArray(custom) ? custom : [];
    const text = String(raw).replace(/\r\n/g, '\n').replace(/\n+$/, '');
    const lines = text.split('\n');

    let i = 0;
    const args = {};
    let nameLines = 0;
    for (; i < lines.length; i++) {
      if (MARK.test(lines[i])) break;
      const m = lines[i].match(/^\s*([a-z][a-z0-9_]*)\s*:\s?(.*)$/i);
      if (m) {
        if (m[1].toLowerCase() === 'name') nameLines++;
        args[m[1].toLowerCase()] = m[2].trim();
      }
    }
    if (nameLines > 1) {
      throw new Error('"name:" appears more than once. The first "name:" line selects the tool, so no parameter can be called name (use the parameter names listed for the tool)');
    }

    const name = args.name;
    delete args.name;
    if (!name) throw new Error('missing a "name:" line');
    const isCustom = !KNOWN.includes(name);
    if (isCustom && !customNames.includes(name)) {
      throw new Error(`unknown tool "${name}". Available: ${KNOWN.concat(customNames).join(', ')}`);
    }

    let rest = lines.slice(i);

    // write_file and replace_in_file must finish with =====END=====. Without it the
    // reply may have been cut off, and running half a file would damage the project.
    if (name === 'write_file' || name === 'replace_in_file') {
      let last = rest.length - 1;
      while (last >= 0 && rest[last].trim() === '') last--;
      if (last < 0 || !/^=====END=====\s*$/.test(rest[last])) {
        throw new Error('block is incomplete: it must finish with a line "=====END=====". The reply may have been cut off, so nothing was changed. Send the whole block again.');
      }
      rest = rest.slice(0, last);
    }

    // Skill tools: header lines, plus an optional =====BODY=====  ...  =====END===== section.
    if (isCustom) {
      const at = rest.findIndex((l) => MARK.test(l));
      if (at !== -1) {
        const kind = rest[at].match(MARK)[1];
        if (kind !== 'BODY') throw new Error(`unknown section =====${kind}=====. Skill tools only accept =====BODY=====`);
        let last = rest.length - 1;
        while (last > at && rest[last].trim() === '') last--;
        if (last === at || !/^=====END=====\s*$/.test(rest[last])) {
          throw new Error('block is incomplete: a =====BODY===== section must finish with a line "=====END=====". The reply may have been cut off, so nothing was run. Send the whole block again.');
        }
        const body = rest.slice(at + 1, last).join('\n');
        args.body = body.length ? body + '\n' : '';
      }
    }

    if (name === 'write_file') {
      const m = rest.length ? rest[0].match(MARK) : null;
      if (!m || m[1] !== 'CONTENT') {
        throw new Error('write_file needs a line "=====CONTENT=====" followed by the file text');
      }
      const body = rest.slice(1).join('\n');
      args.content = body.length ? body + '\n' : '';
    }

    if (name === 'replace_in_file') {
      const edits = [];
      let cur = null;
      let mode = null;
      for (const line of rest) {
        const m = line.match(MARK);
        if (m && m[1] === 'SEARCH') {
          cur = { search: [], replace: null };
          edits.push(cur);
          mode = 'search';
        } else if (m && m[1] === 'REPLACE') {
          if (!cur || mode !== 'search') throw new Error('=====REPLACE===== must follow a =====SEARCH===== section');
          cur.replace = [];
          mode = 'replace';
        } else if (cur) {
          (mode === 'search' ? cur.search : cur.replace).push(line);
        }
      }
      if (!edits.length) throw new Error('replace_in_file needs =====SEARCH===== and =====REPLACE===== sections');
      edits.forEach((e, k) => {
        if (e.replace === null) throw new Error(`edit ${k + 1} has SEARCH but no =====REPLACE=====`);
      });
      args.edits = edits.map((e) => ({ search: e.search.join('\n'), replace: e.replace.join('\n') }));
    }

    return { name, args };
  }

  const api = { parseBlock, KNOWN };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DSAgentParser = api;
})(typeof window !== 'undefined' ? window : globalThis);
