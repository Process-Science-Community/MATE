/**
 * Artifact types and artifact addresses - the TS mirror of `mate.sdk.artifacts`.
 *
 * An *artifact type* (`petri_net@1`) is the contract; an *artifact* is one concrete
 * instance a module produced, addressed by an {@link ArtifactRef}. The registry and
 * the json payload shapes live in the generated `artifacts.generated.ts`.
 */
export * from "./artifacts.generated";

/** `{log_id}/{module_id}[@{variant}]/{key}` - Python's `str(ArtifactRef)`. */
export type ArtifactRef = `${string}/${string}/${string}`;

export interface ArtifactRefParts {
  log_id: string;
  module_id: string;
  key: string;
  /** Filter variant of the producer's cache; null = the default (unfiltered) cache. */
  variant: string | null;
}

/** Python's `ArtifactRef.parse`: throws unless *text* is `log_id/module_id[@variant]/key`. */
export function parseArtifactRef(text: string): ArtifactRefParts {
  const parts = text.split("/");
  const [log_id = "", module = "", key = ""] = parts;
  const at = module.indexOf("@");
  const module_id = at < 0 ? module : module.slice(0, at);
  const variant = at < 0 ? null : module.slice(at + 1);
  if (parts.length !== 3 || !log_id || !module_id || !key || variant === "") {
    throw new Error(`${JSON.stringify(text)} is not log_id/module_id[@variant]/key.`);
  }
  return { log_id, module_id, key, variant };
}
