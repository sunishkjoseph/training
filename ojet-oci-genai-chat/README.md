# OCI Generative AI Agent Chat (Oracle JET)

This project scaffolds an Oracle JET single-page application that provides a chat experience for interacting with an Oracle Cloud Infrastructure (OCI) Generative AI Agent. Because the Oracle JET CLI is not available in this environment, the project structure was created manually, but it adheres to the standard Oracle JET conventions so it can be opened locally and enhanced with the official tooling.

## Features

- Redwood-themed chat layout built with Oracle JET components
- Knockout-based view model that streams and renders agent responses
- Service abstraction for calling the OCI Generative AI Agent Chat API with server-sent events
- Externalized configuration (YAML or JSON) for endpoints, agent OCID, and auth headers

## Getting Started

1. **Install dependencies** (requires access to the Oracle JET npm packages):

   ```bash
   npm install
   ```

2. **Provide configuration** by copying one of the sample files in `src/config/` to `app-config.yaml` or `app-config.json` and filling in your OCI details:

   ```bash
   cp src/config/app-config.sample.yaml src/config/app-config.yaml
   # or cp src/config/app-config.sample.json src/config/app-config.json
   ```

   Update the new file with your Generative AI endpoint, agent OCID, and the Authorization header value that should be sent with each request. The application reads the configuration at runtime, so no code changes are required.

3. **Run the development server**:

   ```bash
   npx ojet serve
   ```

4. **Build for production**:

   ```bash
   npx ojet build --release
   ```

## Project Structure

```
src/
  config/                    # Configuration folder with sample YAML/JSON definitions
  css/app.scss            # Redwood theme overrides for the chat shell
  index.html              # Root HTML file bootstrapping the Oracle JET module
  js/
    main.js               # Oracle JET bootstrap logic and router configuration
    service/GenAIService  # Wrapper around the OCI Generative AI Agent API
    viewModels/app.js     # Knockout view model powering the chat
    views/app.html        # Chat UI markup with Oracle JET components
```

## Packaging

Generate a distributable archive from the repository root:

```bash
./scripts/package-archive.sh
```

The script zips the `ojet-oci-genai-chat/` directory into `ojet-oci-genai-chat.zip` while omitting transient directories like `node_modules/`. Provide a filename argument if you would like to change the archive name.

## Limitations

The Oracle JET CLI and npm registry are not accessible from this environment, so the project has not been built or executed here. Use a local environment with network access to install dependencies and run the application.
