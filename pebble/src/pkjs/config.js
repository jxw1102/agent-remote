module.exports = [
  {
    type: 'heading',
    defaultValue: 'Agent Remote'
  },
  {
    type: 'section',
    items: [
      {
        type: 'heading',
        defaultValue: 'Get started'
      },
      {
        type: 'text',
        defaultValue: 'Install the daemon from https://github.com/jxw1102/agent-remote (README / Get started). Paste the printed Base URL and token below. Use the host LAN / Tailscale IP, not 127.0.0.1 (that is this phone).'
      },
      {
        type: 'input',
        id: 'daemon_url',
        defaultValue: '',
        label: 'Daemon URL (LAN IP of the host, not 127.0.0.1)',
        attributes: {
          placeholder: 'http://192.168.x.x:8473'
        }
      },
      {
        type: 'input',
        id: 'token',
        defaultValue: '',
        label: 'Token'
      },
      {
        type: 'select',
        id: 'font_size',
        defaultValue: '1',
        label: 'Transcript size',
        options: [
          { label: 'Small', value: '0' },
          { label: 'Medium', value: '1' },
          { label: 'Large', value: '2' }
        ]
      },
      {
        type: 'slider',
        id: 'poll_work',
        defaultValue: 4,
        min: 3,
        max: 8,
        step: 1,
        label: 'Poll while working (s)'
      },
      {
        type: 'slider',
        id: 'poll_idle',
        defaultValue: 25,
        min: 15,
        max: 45,
        step: 1,
        label: 'Poll while idle (s)'
      },
      {
        type: 'text',
        defaultValue: 'The watch never stores the token. URL, token, and poll stay on the phone. Leave URL empty until the daemon is running — the watch shows setup.'
      }
    ]
  },
  {
    type: 'submit',
    defaultValue: 'Save'
  }
];
