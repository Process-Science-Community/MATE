package mate.sdk;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import mate.sdk.internal.ProxyContext;
import mate.sdk.internal.RpcTransport;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Context behaviour against a recording stub transport - no socket needed. */
class ProxyContextTest {

    static final class StubTransport implements RpcTransport {
        final List<Map.Entry<String, Map<String, Object>>> calls = new ArrayList<>();
        final List<String> fireAndForget = new ArrayList<>();
        Object nextResult;
        Throwable nextError;

        @Override
        public Object request(String method, Map<String, Object> params) {
            calls.add(Map.entry(method, params));
            if (nextError != null) {
                Throwable t = nextError;
                nextError = null;
                if (t instanceof RuntimeException runtime) {
                    throw runtime;
                }
                if (t instanceof Error error) {
                    throw error;
                }
                throw new RuntimeException(t);
            }
            return nextResult;
        }

        @Override
        public void requestFireAndForget(String method, Map<String, Object> params) {
            calls.add(Map.entry(method, params));
            fireAndForget.add(method);
        }
    }

    private static Map<String, Object> meta() {
        Map<String, Object> m = new HashMap<>();
        m.put("log_id", "log1");
        m.put("module_id", "conformance");
        m.put("workdir", "/tmp/work");
        m.put("config", Map.of("threshold", 3));
        m.put("capabilities", List.of("discovery", "other.cap"));
        m.put("cache_dir", "/tmp/cache");
        // events_path / cases_path deliberately absent -> data-walled
        return m;
    }

    @Test
    void scalarsAnswerLocally() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        assertEquals("log1", ctx.logId());
        assertEquals("conformance", ctx.moduleId());
        assertEquals(3, ctx.config().get("threshold"));
        assertTrue(ctx.registry().has("discovery"));
        // `read_only` absent (an older host) reads as false; present, it is served locally.
        assertEquals(false, ctx.readOnly());
        Map<String, Object> shared = meta();
        shared.put("read_only", true);
        assertTrue(new ProxyContext(transport, "tok", shared).readOnly());
        assertEquals(0, transport.calls.size());
    }

    @Test
    void missingSnapshotKeysAreDataWalled() {
        ProxyContext ctx = new ProxyContext(new StubTransport(), "tok", meta());
        assertThrows(DataWallException.class, () -> ctx.eventLog().eventsPath());
        assertThrows(DataWallException.class, () -> ctx.eventLog().casesPath());
    }

    @Test
    void cacheEnvelopes() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());

        transport.nextResult = Map.of("kind", "json", "value", Map.of("a", 1));
        Optional<Object> got = ctx.cache().getJson("model");
        assertTrue(got.isPresent());

        transport.nextResult = Map.of("kind", "pickle", "path", "/x.pkl");
        assertThrows(UnsupportedCacheValueException.class, () -> ctx.cache().getJson("model"));

        ctx.cache().setJson("model", List.of(1, 2));
        Map<String, Object> setParams = transport.calls.get(transport.calls.size() - 1).getValue();
        assertEquals("tok", setParams.get("ctx_token"));
        @SuppressWarnings("unchecked")
        Map<String, Object> envelope = (Map<String, Object>) setParams.get("value");
        assertEquals("json", envelope.get("kind"));
        assertEquals(List.of(1, 2), envelope.get("value"));
    }

    @Test
    void checkCancelledPropagatesCancellation() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        transport.nextError = new CancelledException();
        assertThrows(CancelledException.class, ctx::checkCancelled);
        assertEquals("ctx.cancel.check", transport.calls.get(0).getKey());
    }

    @Test
    void progressAndLoggerShapes() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());

        ctx.progress().update(0.5, 10.0, "mining", "halfway");
        Map<String, Object> progressParams = transport.calls.get(0).getValue();
        assertEquals(0.5, progressParams.get("current"));
        assertEquals(10.0, progressParams.get("total"));
        assertEquals("mining", progressParams.get("stage"));
        assertEquals("halfway", progressParams.get("message"));

        ctx.logger().info("did_thing", Map.of("n", 2));
        assertEquals(List.of("ctx.logger.log"), transport.fireAndForget);
        Map<String, Object> logParams = transport.calls.get(1).getValue();
        assertEquals("info", logParams.get("level"));
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = (Map<String, Object>) logParams.get("payload");
        assertEquals("did_thing", payload.get("event"));
        assertEquals(2, payload.get("n"));
    }

    private static Map<String, Object> port(String type, String format, Path path, String ref) {
        Map<String, Object> entry = new HashMap<>();
        entry.put("artifact_type", type);
        entry.put("format", format);
        entry.put("path", path == null ? null : path.toString());
        entry.put("ref", ref);
        return entry;
    }

    @Test
    void inputsReadSnapshotPaths(@TempDir Path dir) throws Exception {
        Path model = Files.writeString(dir.resolve("petri_net.json"), "{\"places\": [{\"id\": \"p1\"}]}");
        Path blob = Files.write(dir.resolve("raw.bin"), new byte[] {1, 2});
        Path table = dir.resolve("table.parquet");
        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("model", port("petri_net", "json", model, "log1/discovery/petri_net"));
        inputs.put("raw", port("table", "bytes", blob, "log1/other/raw"));
        inputs.put("table", port("table", "parquet", table, "log1/other/table"));
        inputs.put("unbound", port("dfg", null, null, null));
        Map<String, Object> m = meta();
        m.put("inputs", inputs);
        StubTransport transport = new StubTransport();
        Inputs in = new ProxyContext(transport, "tok", m).inputs();

        assertEquals(List.of("model", "raw", "table", "unbound"), in.ports());
        assertEquals(Optional.of("log1/discovery/petri_net"), in.ref("model"));
        assertEquals(Map.of("places", List.of(Map.of("id", "p1"))), in.get("model").orElseThrow());
        assertArrayEquals(new byte[] {1, 2}, (byte[]) in.get("raw").orElseThrow());
        assertEquals(Optional.of(table), in.path("table"));
        assertThrows(IllegalStateException.class, () -> in.get("table"));
        assertTrue(in.get("unbound").isEmpty() && in.ref("unbound").isEmpty());
        assertTrue(in.get("undeclared").isEmpty());
        assertEquals(0, transport.calls.size());

        // An older host sends no `inputs` key: no ports.
        assertEquals(List.of(), new ProxyContext(transport, "tok", meta()).inputs().ports());
    }

    @Test
    void duckdbRowsCast() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        transport.nextResult = List.of(List.of("c1", "a"), List.of("c1", "b"));
        List<List<Object>> rows = ctx.eventLog().duckdbFetch("SELECT 1", null);
        assertEquals(2, rows.size());
        assertEquals("a", rows.get(0).get(1));
        Map<String, Object> params = transport.calls.get(0).getValue();
        assertEquals(List.of(), params.get("params"));
    }
}
