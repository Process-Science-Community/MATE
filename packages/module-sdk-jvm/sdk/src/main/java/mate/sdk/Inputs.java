package mate.sdk;

import java.nio.file.Path;
import java.util.List;
import java.util.Optional;

/**
 * The manifest's {@code inputs:} ports, each bound by the platform to another
 * module's artifact (protocol §6 {@code inputs}). Answers from the per-call
 * snapshot and reads the producer's cache file straight off the shared
 * filesystem - no round-trip. Only the producer's default (unfiltered) cache
 * entry is bound.
 *
 * <p>A port is empty while it is unbound (no {@code from:} producer the user
 * owns) or the producer has not produced the artifact yet.
 */
public interface Inputs {

    /** The ids of the manifest's input ports. */
    List<String> ports();

    /** The bound artifact's address, {@code log_id/module_id[@variant]/key}. */
    Optional<String> ref(String port);

    /**
     * The artifact: a JSON value (Map/List/String/Number/Boolean) for
     * {@code json} artifacts, {@code byte[]} for {@code bytes} artifacts.
     *
     * @throws IllegalStateException for a {@code parquet} artifact - read
     *     {@link #path} with any Parquet/Arrow library instead
     */
    Optional<Object> get(String port);

    /** Absolute path of the bound producer's cache file (read-only), any format. */
    Optional<Path> path(String port);
}
