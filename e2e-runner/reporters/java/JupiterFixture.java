import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import io.testlookup.junit5.TestLookupExtension;
import static org.junit.jupiter.api.Assertions.assertEquals;

@ExtendWith(TestLookupExtension.class)
public class JupiterFixture {
    @Test void release_pass() { assertEquals(4, 2 + 2); }
    @Test void release_fail() { assertEquals(5, 2 + 2); }
}
