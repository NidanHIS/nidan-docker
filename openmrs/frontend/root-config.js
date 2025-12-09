import { registerApplication, start } from "single-spa";

// This is a minimal root-config for OpenMRS 3.0
// The actual root-config is typically provided by @openmrs/esm-root-config
// but for now we'll use a basic implementation

registerApplication({
  name: "@openmrs/esm-root-config",
  app: () => System.import("@openmrs/esm-root-config"),
  activeWhen: ["/openmrs"],
});

start({
  urlRerouteOnly: true,
});

