import json
import random
import uuid
import time
from typing import List, Tuple

def generate_mock_allure_results(num_tests: int, failure_rate: float, project_id: str, build_number: str) -> List[Tuple[str, bytes]]:
    """Generates synthetic Allure *-result.json files. Returns a list of (Key, Bytes)."""
    files = []
    
    components = ["Authentication", "Checkout", "User Profile", "Search", "Inventory"]
    features = ["Login", "Payment", "Settings", "Full-text Search", "Stock Update"]
    owners = ["backend-team", "frontend-team", "qa-automation"]
    
    current_time = int(time.time() * 1000)

    for i in range(num_tests):
        test_uuid = str(uuid.uuid4())
        is_failed = random.random() < failure_rate
        
        status = "failed" if is_failed else "passed"
        if not is_failed and random.random() < 0.05:
            status = "skipped"
        
        start_time = current_time - random.randint(1000, 60000)
        stop_time = start_time + random.randint(50, 5000)
        
        component = random.choice(components)
        feature = random.choice(features)
        
        payload = {
            "uuid": test_uuid,
            "name": f"test_{component.lower().replace(' ', '_')}_{i}",
            "fullName": f"com.testlookup.tests.{component.replace(' ', '')}Test.test_{i}",
            "status": status,
            "start": start_time,
            "stop": stop_time,
            "labels": [
                {"name": "suite", "value": f"{component} Suite"},
                {"name": "testClass", "value": f"com.testlookup.tests.{component.replace(' ', '')}Test"},
                {"name": "package", "value": "com.testlookup.tests"},
                # Use canonical Severity enum values (uppercase) — see
                # app.models.postgres.Severity. Lowercase / unknown values
                # ("normal", etc.) used to silently break TestCaseSummary
                # serialization on the run detail page.
                {"name": "severity", "value": random.choice(["BLOCKER", "CRITICAL", "MAJOR", "MINOR"])},
                {"name": "feature", "value": feature},
                {"name": "epic", "value": f"{component} Epic"},
                {"name": "owner", "value": random.choice(owners)},
                {"name": "tag", "value": "automated"},
                {"name": "tag", "value": "regression"}
            ],
            "steps": [
                {
                    "name": "Navigate to application",
                    "status": "passed",
                    "start": start_time,
                    "stop": start_time + 100
                },
                {
                    "name": "Perform test action",
                    "status": status,
                    "start": start_time + 100,
                    "stop": stop_time
                }
            ],
            "attachments": []
        }
        
        if status == "failed":
            payload["statusDetails"] = {
                "message": "AssertionError: Expected true but found false",
                "trace": f"java.lang.AssertionError: Expected true but found false\n\tat com.testlookup.tests.{component.replace(' ', '')}Test.test_{i}({component.replace(' ', '')}Test.java:42)"
            }
        
        key = f"allure/{test_uuid}-result.json"
        content = json.dumps(payload).encode("utf-8")
        files.append((key, content))
        
    return files

def generate_mock_testng_results(num_tests: int, failure_rate: float, project_id: str, build_number: str) -> List[Tuple[str, bytes]]:
    """Generates synthetic TestNG XML files. Returns a list of (Key, Bytes)."""
    
    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    
    root = ET.Element("testsuite", name="Mock TestNG Suite", tests=str(num_tests))
    
    classes = ["LoginTest", "RegistrationTest", "CartTest", "CheckoutTest"]
    
    for i in range(num_tests):
        is_failed = random.random() < failure_rate
        duration_s = random.uniform(0.1, 5.0)
        
        cls_name = random.choice(classes)
        test_name = f"verify{cls_name.replace('Test', '')}Flow_{i}"
        
        testcase = ET.SubElement(root, "testcase", name=test_name, classname=f"com.example.tests.{cls_name}", time=f"{duration_s:.3f}")
        
        if is_failed:
            failure = ET.SubElement(testcase, "failure", message="Element not found exceptions")
            failure.text = f"org.openqa.selenium.NoSuchElementException: Unable to locate element\n\tat com.example.tests.{cls_name}.{test_name}({cls_name}.java:{random.randint(20, 100)})"
        elif random.random() < 0.05:
            ET.SubElement(testcase, "skipped")
            
    # Pretty print XML
    xmlstr = minidom.parseString(ET.tostring(root)).toprettyxml(indent="    ")
    
    key = f"testng/testng-results-{build_number}.xml"
    content = xmlstr.encode("utf-8")
    
    return [(key, content)]
