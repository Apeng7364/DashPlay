import com.playport.server.ProcessByteStream;
import java.util.Arrays;

/** Synthetic JVM -> helper -> private D-Bus -> socketpair roundtrip, not iPhone traffic. */
public class JvmBridgeProbe {
    public static void main(String[] args) throws Exception {
        Process child = new ProcessBuilder(args).start();
        try (ProcessByteStream stream = new ProcessByteStream(child, "linux-bridge-test")) {
            byte[] sent = new byte[131072];
            for (int i = 0; i < sent.length; i++) sent[i] = (byte) i;
            stream.send(sent);
            byte[] received = new byte[sent.length];
            int offset = 0;
            while (offset < received.length) {
                byte[] part = stream.recv(Math.min(137, received.length - offset), 3000);
                if (part == null || part.length == 0) throw new AssertionError("early EOF or timeout");
                System.arraycopy(part, 0, received, offset, part.length);
                offset += part.length;
            }
            if (!Arrays.equals(sent, received)) throw new AssertionError("reordered or corrupted bytes");
            System.out.println("PASS JVM-BlueZ bridge binary roundtrip");
        }
        if (!child.waitFor(5, java.util.concurrent.TimeUnit.SECONDS)) {
            child.destroyForcibly();
            throw new AssertionError("helper did not stop");
        }
    }
}
