curl -X POST http://192.168.9.249:8085/api/eval/7/0 \
  -H 'Content-Type: application/json' \
  -d '{"js": "var c=document.createElement(\"canvas\");var gl=c.getContext(\"webgl2\")||c.getContext(\"webgl\");gl?\"WebGL OK: \"+gl.getParameter(gl.RENDERER):\"No WebGL\""}'