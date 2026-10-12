import java.io.*;
import java.security.*;
import java.security.cert.Certificate;

/** Copy the receiver TLS key and certificate into a fresh PKCS12 (iterations from security properties). */
public class KeystoreRewrap {
    public static void main(String[] a) throws Exception {
        char[] pw = "playport".toCharArray();   // the server's fixed keystore password
        KeyStore src = KeyStore.getInstance("PKCS12");
        try (InputStream in = new FileInputStream(a[0])) { src.load(in, pw); }
        Key key = src.getKey("playport", pw);
        Certificate[] chain = src.getCertificateChain("playport");
        KeyStore dst = KeyStore.getInstance("PKCS12");
        dst.load(null, null);
        dst.setKeyEntry("playport", key, pw, chain);
        try (OutputStream out = new FileOutputStream(a[1])) { dst.store(out, pw); }
        System.out.println("rewrapped alias=playport chain=" + chain.length);
    }
}
