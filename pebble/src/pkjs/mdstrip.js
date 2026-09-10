function stripMarkdown(s) {
  s = String(s || '');
  s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  s = s.replace(/```[\w+-]*\n?([\s\S]*?)```/g, function (_, inner) {
    return String(inner || '').replace(/^\n|\n$/g, '');
  });
  s = s.replace(/~~~[\w+-]*\n?([\s\S]*?)~~~/g, function (_, inner) {
    return String(inner || '').replace(/^\n|\n$/g, '');
  });
  s = s.replace(/```+|~~~+/g, '');
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  s = s.replace(/^[ \t]+|[ \t]+$/gm, '');
  s = s.replace(/[ \t]+/g, ' ');
  s = s.replace(/\n{3,}/g, '\n\n');
  s = s.replace(/^\n+|\n+$/g, '');
  return s;
}

function decorateOff(s) {
  s = String(s || '');
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1');
  s = s.replace(/__([^_]+)__/g, '$1');
  s = s.replace(/`([^`]+)`/g, '$1');
  s = s.replace(/^#{1,6}\s+/gm, '');
  s = s.replace(/^[-*+]\s+/gm, '');
  s = s.replace(/^\d+\.\s+/gm, '');
  return s;
}

module.exports = {
  stripMarkdown: stripMarkdown,
  decorateOff: decorateOff
};
