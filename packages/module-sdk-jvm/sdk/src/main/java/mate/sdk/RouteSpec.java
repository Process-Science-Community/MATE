package mate.sdk;

import java.util.Locale;

/**
 * An HTTP route the platform mounts at {@code /api/v1/modules/{module_id}{path}}.
 *
 * <p>Bridged routes take no typed request bodies and no declared query
 * parameters (protocol §9) - the module's per-user config is the input
 * channel. The handler's return value (any JSON-representable object) becomes
 * the response body.
 */
public final class RouteSpec {

    private final String method;
    private final String path;
    private final String name;
    private final boolean viewerSafe;

    private RouteSpec(String method, String path, String name, boolean viewerSafe) {
        if (path == null || !path.startsWith("/")) {
            throw new IllegalArgumentException("route path must start with '/': " + path);
        }
        this.method = method;
        this.path = path;
        this.name = name;
        this.viewerSafe = viewerSafe;
    }

    public static RouteSpec get(String path) {
        return new RouteSpec("GET", path, null, false);
    }

    public static RouteSpec post(String path) {
        return new RouteSpec("POST", path, null, false);
    }

    public static RouteSpec put(String path) {
        return new RouteSpec("PUT", path, null, false);
    }

    public static RouteSpec patch(String path) {
        return new RouteSpec("PATCH", path, null, false);
    }

    public static RouteSpec delete(String path) {
        return new RouteSpec("DELETE", path, null, false);
    }

    /** Optional display name surfaced in the platform's route metadata. */
    public RouteSpec named(String name) {
        return new RouteSpec(this.method, this.path, name, this.viewerSafe);
    }

    /**
     * Let a shared dashboard's viewer ({@code ctx.readOnly()}) call this non-GET
     * route; the platform answers 403 otherwise. Only for a route that reads, or
     * caches a result derived deterministically from the owner's data and the
     * request - never one that changes owner state.
     */
    public RouteSpec viewerSafe() {
        return new RouteSpec(this.method, this.path, this.name, true);
    }

    public String method() {
        return method.toUpperCase(Locale.ROOT);
    }

    public String path() {
        return path;
    }

    public String name() {
        return name;
    }

    public boolean isViewerSafe() {
        return viewerSafe;
    }
}
