package onecloud;

import java.lang.reflect.InvocationTargetException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import org.bouncycastle.crypto.digests.SHA512Digest;
import org.bouncycastle.crypto.generators.HKDFBytesGenerator;
import org.bouncycastle.crypto.modes.ChaCha20Poly1305;
import org.bouncycastle.crypto.params.AEADParameters;
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters;
import org.bouncycastle.crypto.params.HKDFParameters;
import org.bouncycastle.crypto.params.KeyParameter;
import org.bouncycastle.crypto.params.X25519PrivateKeyParameters;
import org.bouncycastle.crypto.signers.Ed25519Signer;

/**
 * Starts the PlayPort server unchanged, while a background thread exercises the handshake crypto
 * with throwaway keys. On the S805 the first P-256 signature in a JVM took 3.3 s (class loading and
 * static init), which sat on the iPhone's MFi challenge during every boot (2026-10-10 profiling).
 * Warming from JVM start competed with server start-up (RTSP ready 5.4 s -> 8.4 s), so it now waits
 * until the AirPlay port accepts connections; the MFi challenge arrives ~3 s after that.
 *
 * Build: javac --release 21 -cp bcprov-jdk18on-1.79.jar -d out onecloud/Launcher.java
 *        jar cf onecloud-launcher.jar -C out .
 */
public final class Launcher {
    public static void main(String[] args) throws Throwable {
        Thread warm = new Thread(() -> { awaitAirPlay(args); warmUp(); }, "crypto-warmup");
        warm.setDaemon(true);
        warm.start();
        try {
            Class.forName("com.playport.server.MainKt").getMethod("main", String[].class)
                    .invoke(null, (Object) args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }

    /** Wait (max 30 s) until the server's AirPlay listener on --bind:7000 accepts a connection. */
    private static void awaitAirPlay(String[] args) {
        String host = null;
        for (int i = 0; i + 1 < args.length; i++) if (args[i].equals("--bind")) host = args[i + 1];
        if (host == null) return;
        long deadline = System.nanoTime() + 30_000_000_000L;
        while (System.nanoTime() < deadline) {
            try (Socket probe = new Socket()) {
                probe.connect(new InetSocketAddress(host, 7000), 200);
                return;
            } catch (Exception notYet) {
                try { Thread.sleep(200); } catch (InterruptedException e) { return; }
            }
        }
    }

    private static void warmUp() {
        try {
            long start = System.nanoTime();
            SecureRandom random = new SecureRandom();
            byte[] data = new byte[1024];
            random.nextBytes(data);
            KeyPairGenerator ec = KeyPairGenerator.getInstance("EC");
            ec.initialize(new ECGenParameterSpec("secp256r1"));
            KeyPair p256 = ec.generateKeyPair();
            for (int i = 0; i < 1; i++) {   // first use dominates; more rounds gained little
                // MFi challenge path: raw 32-byte digest signed with NONEwithECDSA.
                Signature signer = Signature.getInstance("NONEwithECDSA");
                signer.initSign(p256.getPrivate());
                signer.update(data, 0, 32);
                byte[] signature = signer.sign();
                Signature verifier = Signature.getInstance("NONEwithECDSA");
                verifier.initVerify(p256.getPublic());
                verifier.update(data, 0, 32);
                verifier.verify(signature);
                // AirPlay pair-verify path (BouncyCastle): X25519, Ed25519, HKDF-SHA512, ChaCha20-Poly1305.
                X25519PrivateKeyParameters a = new X25519PrivateKeyParameters(random);
                X25519PrivateKeyParameters b = new X25519PrivateKeyParameters(random);
                byte[] shared = new byte[X25519PrivateKeyParameters.SECRET_SIZE];
                a.generateSecret(b.generatePublicKey(), shared, 0);
                Ed25519PrivateKeyParameters ed = new Ed25519PrivateKeyParameters(random);
                Ed25519Signer edSigner = new Ed25519Signer();
                edSigner.init(true, ed);
                edSigner.update(data, 0, 64);
                edSigner.generateSignature();
                HKDFBytesGenerator hkdf = new HKDFBytesGenerator(new SHA512Digest());
                hkdf.init(new HKDFParameters(shared, data, data));
                byte[] key = new byte[32];
                hkdf.generateBytes(key, 0, key.length);
                ChaCha20Poly1305 aead = new ChaCha20Poly1305();
                aead.init(true, new AEADParameters(new KeyParameter(key), 128, new byte[12]));
                byte[] out = new byte[aead.getOutputSize(data.length)];
                aead.doFinal(out, aead.processBytes(data, 0, data.length, out, 0));
            }
            System.out.println("crypto warm-up done in " + (System.nanoTime() - start) / 1_000_000 + " ms");
        } catch (Exception e) {
            // Warm-up is an optimisation only; the server still works without it.
            System.out.println("crypto warm-up skipped: " + e);
        }
    }

    private Launcher() {}
}
