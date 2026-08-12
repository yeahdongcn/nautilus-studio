# Third-party software and models

Nautilus Studio is licensed under Apache-2.0. Its direct runtime and web
dependencies retain their own licenses.

## Python runtime dependencies

| Package | Declared license |
| --- | --- |
| FastAPI | MIT |
| HTTPX | BSD-3-Clause |
| Pillow | MIT-CMU |
| Pydantic | MIT |
| python-multipart | Apache-2.0 |
| tomli | MIT |
| Uvicorn | BSD-3-Clause |

## Creator UI dependencies

| Package | Locked version | Declared license |
| --- | ---: | --- |
| React | 18.3.1 | MIT |
| React DOM | 18.3.1 | MIT |
| Framer Motion | 11.18.2 | MIT |
| Lucide React | 0.460.0 | ISC |
| Vite | 6.4.3 | MIT |
| Tailwind CSS | 3.4.19 | MIT |
| PostCSS | 8.5.26 | MIT |
| Autoprefixer | 10.5.4 | MIT |
| Prettier | 3.9.6 | MIT |

The JavaScript versions above come from `web/package-lock.json`. Run the
project's dependency scanner again whenever that lockfile changes.

## Model servers and checkpoints

Nautilus does not redistribute MiniMax-H3, Qwen-Image-Edit, vLLM-Omni, or a
hosted provider's model weights. They are independently deployed services.
Operators are responsible for reviewing each framework, checkpoint, dataset,
and vendor API license before use or redistribution.

This file is an engineering inventory, not legal advice and not a replacement
for the full license text shipped by each dependency.
