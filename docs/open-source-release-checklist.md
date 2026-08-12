# Open-source release checklist

## Ready in the local tree

- [x] Apache-2.0 license and contribution policy;
- [x] security boundary and credential handling guidance;
- [x] deterministic planner fallback;
- [x] provider-neutral image-edit contract;
- [x] 2511 acceptance evidence template (to be filled only after real hardware validation);
- [x] local/vendor configuration examples;
- [x] Dockerfile and Compose entry point;
- [x] Python tests, lint, format, Node syntax checks, and web build CI;
- [x] React/Vite creator workspace preview on port 5173;
- [x] dialog-based editing for director direction, project bible, shots, and material metadata;
- [x] packaged React assets served under `/assets` by the Python app;
- [x] no private registry, host, token, or model path in public source files;
- [x] generated evidence and internal TODO files are ignored and CI rejects
      tracked generated/private-path content;
- [x] asset content is constrained to configured media/import roots and output
      listings do not expose absolute filesystem paths;
- [x] media-tool wrapper requires an operator-supplied image instead of a
      private default.
- [x] Docker image runs as a non-root user and exposes a healthcheck;
- [x] CI runs Python and npm dependency audits;
- [x] CHANGELOG and contribution/review templates are present;
- [x] one-command contributor check (`make check`) is present;

## Required before publishing

- [x] replace the Apache boilerplate copyright placeholder with the legal
      copyright holder and year;
- [x] choose the canonical GitHub owner and repository name;
- [x] replace placeholder repository URLs when known;
- [x] publish the React shell as the Docker default and retain the vanilla
      source-install fallback;
- [ ] add screenshots or a short demo video with permission to redistribute;
- [ ] document the exact public model/API examples that can be reproduced;
- [x] run a direct dependency/license scan for Python and web packages;
- [ ] run a transitive SBOM/license scan in the publication CI environment;
- [ ] review generated examples for private media and sensitive prompts.

The current local validation (`make test`, `make lint`, and `npm run format &&
npm run build`) passes. `npm audit` still needs a network-available rerun before
the direct dependency item is treated as fresh release evidence.

## Required before hosting a public instance

- [ ] add authentication and authorization;
- [ ] add rate limiting, request/body limits, and abuse controls;
- [ ] configure TLS, content policy, and tenant-isolated storage;

## Provider acceptance evidence

For each image-edit provider, preserve a small local evidence bundle containing
the model revision, request JSON shape (without keys), ordered reference
manifest, output dimensions, and a human review note. Do not commit private
images or provider responses to the repository.
