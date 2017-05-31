Feature: Get information about ports which will be forwarded from source

Scenario: Return default port detected on source machine - discovered ports only
   Given the local virtual machines:
         | name       | definition          | ensure_fresh |
         | source     | centos6-guest-httpd | no           |
         | target     | centos7-target      | no           |
     Then get list of discovered ports from source which will be forwared from target

Scenario: Return default port detected on source machine - override port 80
   Given the local virtual machines:
         | name       | definition          | ensure_fresh |
         | source     | centos6-guest-httpd | no           |
         | target     | centos7-target      | no           |
     Then get list of discovered ports on source which will be forwarded from target and override port 80 to 8080

Scenario: Return default port detected on source machine - add port 11111
   Given the local virtual machines:
         | name       | definition          | ensure_fresh |
         | source     | centos6-guest-httpd | no           |
         | target     | centos7-target      | no           |
     Then get list of discovered ports on source which will be forwarded from target and add port 11111 to 11111

Scenario: Disable default port mapping and return user defined ports - port 11111, port 11112
   Given the local virtual machines:
         | name       | definition          | ensure_fresh |
         | source     | centos6-guest-httpd | no           |
         | target     | centos7-target      | no           |
     Then get list of user defined ports from source which will be forwarded from target - port 11111, port 11112
