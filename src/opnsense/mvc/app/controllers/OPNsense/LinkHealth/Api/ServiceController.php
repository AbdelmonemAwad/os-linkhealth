<?php

/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\LinkHealth\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\LinkHealth\LinkHealth;

class ServiceController extends ApiControllerBase
{
    /**
     * How long a socket blinks when the caller asks for no particular time. The script has a
     * default of its own, but it cannot be reached from here: its configd action takes two
     * parameters, and a parameter left empty arrives as an empty string rather than as an absent
     * argument - which is not a number, and the action fails outright. So the wait is named here
     * as well, and the two are deliberately the same half minute.
     */
    private const IDENTIFY_SECONDS = 30;

    /**
     * The longest blink the worker will honour. It clamps anything larger down to this silently,
     * which would leave the page counting down from an hour while the LED went dark after five
     * minutes, so a request past the ceiling is refused here instead of quietly rounded off.
     */
    private const IDENTIFY_SECONDS_MAX = 300;

    private function run($action, $params = [])
    {
        return (new Backend())->configdpRun('linkhealth ' . $action, $params, false, 300);
    }

    /**
     * a port name is handed to configd and ends up on a command line, so only the shape the
     * kernel actually gives an interface is let through: a driver name and its unit number
     */
    private function validInterface($name)
    {
        return is_string($name) && preg_match('/^[a-z][a-z0-9]*[0-9]$/', $name);
    }

    /**
     * The address the test floods is given to ping, so it has to be an address and nothing else.
     * Which addresses are reachable behind which port is the collector's knowledge, not ours, so
     * that part of the check is left where the bridge table is read.
     */
    private function validTarget($address)
    {
        return is_string($address) && filter_var($address, FILTER_VALIDATE_IP) !== false;
    }

    /**
     * A blink length is a plain count of seconds and nothing else - no sign, no decimal point,
     * nothing that could reach a command line as anything but digits.
     */
    private function validSeconds($seconds)
    {
        return is_string($seconds)
            && ctype_digit($seconds)
            && (int)$seconds >= 1
            && (int)$seconds <= self::IDENTIFY_SECONDS_MAX;
    }

    private function response($output)
    {
        $response = json_decode($output, true);
        return is_array($response) ? $response : ['status' => 'failed', 'detail' => gettext('No response from the link health script.')];
    }

    /**
     * the reading the collector last wrote, for every port at once
     */
    public function statusAction()
    {
        return $this->response($this->run('status'));
    }

    /**
     * The front of the appliance as the plugin draws it, every socket joined to what it is doing.
     *
     * Handed back exactly as the script wrote it. Which socket sits in which row is the layout
     * file's knowledge; a controller that rearranged any of it here would become a second place
     * where the drawing has to be kept right, and the drawing is the part nobody can check from
     * a desk.
     */
    public function faceplateAction()
    {
        return $this->response($this->run('faceplate'));
    }

    /**
     * everything known about one port, including the parts the overview leaves out
     */
    public function detailAction($if = null)
    {
        if (!$this->validInterface($if)) {
            return ['status' => 'failed', 'detail' => gettext('Unknown port.')];
        }
        return $this->response($this->run('detail', [$if]));
    }

    /**
     * The address saved for this port on the settings tab, used when the caller named none.
     */
    private function configuredNeighbour($if)
    {
        foreach ((new LinkHealth())->ports->port->iterateItems() as $port) {
            if ((string)$port->interface === $if) {
                return (string)$port->neighbour;
            }
        }
        return '';
    }

    /**
     * Flood the port with traffic and read its counters again, for a port too quiet to judge.
     *
     * The worker outlives this request on purpose - the test runs for minutes and no browser
     * should be made to hold a connection open that long - so this only reports whether it was
     * able to start. What it measured is collected afterwards from test_status.
     */
    public function runTestAction($if = null)
    {
        if (!$this->request->isPost() || !$this->validInterface($if)) {
            return ['status' => 'failed', 'detail' => gettext('Unknown port.')];
        }
        $target = (string)$this->request->getPost('target', 'string', '');
        if ($target === '') {
            $target = $this->configuredNeighbour($if);
        }
        if ($target !== '' && !$this->validTarget($target)) {
            return ['status' => 'failed', 'detail' => gettext('That is not an address the test can be sent to.')];
        }
        /* an empty second argument leaves the choice of neighbour to the collector, which knows
           which addresses it has actually seen behind this port */
        return $this->response($this->run('test', [$if, $target]));
    }

    /**
     * how the test on this port is getting on, polled by the page while it runs
     */
    public function testStatusAction($if = null)
    {
        if (!$this->validInterface($if)) {
            return ['status' => 'failed', 'detail' => gettext('Unknown port.')];
        }
        $result = $this->response($this->run('testresult', [$if]));
        /* The worker reports a test that could not run as "error" with its reason in "message".
           The page knows one word for that, and shows whatever "detail" carries underneath it, so
           the two are lined up here rather than leaving the reason unread on the floor. */
        if (($result['status'] ?? '') === 'error') {
            $result['status'] = 'failed';
            if (!isset($result['detail']) && isset($result['message'])) {
                $result['detail'] = $result['message'];
            }
        }
        return $result;
    }

    /**
     * Blink one socket's own LED, so that somebody standing in front of the appliance can read
     * the name printed beside it and settle the question the drawing can only claim to answer.
     *
     * POST because it drives the hardware, even though nothing it does outlives the blink. The
     * worker holds the LED for the whole time and detaches at once - no browser should be made to
     * hold a connection open for half a minute - so the answer here says only whether the
     * blinking started, and carries "busy" when another socket already has the LED.
     */
    public function identifyAction($if = null)
    {
        if (!$this->request->isPost() || !$this->validInterface($if)) {
            return ['status' => 'failed', 'detail' => gettext('Unknown port.')];
        }
        /* the sanitiser hands back a string for anything scalar, so a body that sent something
           else - a list, say - arrives as something validSeconds will not accept */
        $seconds = $this->request->getPost('seconds', 'string', '');
        if ($seconds === '') {
            $seconds = (string)self::IDENTIFY_SECONDS;
        } elseif (!$this->validSeconds($seconds)) {
            return ['status' => 'failed',
                    'detail' => sprintf(
                        gettext('A port can be asked to blink for between 1 and %d seconds.'),
                        self::IDENTIFY_SECONDS_MAX
                    )];
        }
        $result = $this->response($this->run('identify', [$if, $seconds]));
        /* The worker answers in one word and explains itself in English, because a script on this
           firewall has no catalogue to translate against and the page is read in Arabic. The page
           shows "detail" in preference to the script's "message", so the single refusal the
           starter can give is said again here, where gettext() reaches it. The word itself is left
           alone: "started" and "busy" are what the page switches on, and "busy" is not a failure. */
        if (($result['status'] ?? '') === 'error') {
            $result['detail'] = gettext(
                'This port has no identification LED: its driver registers no /dev/led entry for it.'
            );
        }
        return $result;
    }

    /**
     * Identify a socket the other way: by making its activity light beat.
     *
     * The identification LED is not universal. On this plugin's reference appliance the 10G cages
     * register a /dev/led node, accept the write and light nothing, because the driver drives one
     * fixed LED index and the board does not wire it - which is not something any interface can be
     * asked about, only found out. Every socket has an activity light, so this beats that one: a
     * second of packets, a second of silence, which is a rhythm ordinary traffic does not have.
     *
     * POST, detached and answered in one word, exactly like the blink, and it takes the same lock,
     * so a socket cannot be beating while another one is blinking.
     */
    public function flickerAction($if = null)
    {
        if (!$this->request->isPost() || !$this->validInterface($if)) {
            return ['status' => 'failed', 'detail' => gettext('Unknown port.')];
        }
        $seconds = $this->request->getPost('seconds', 'string', '');
        if ($seconds === '') {
            $seconds = (string)self::IDENTIFY_SECONDS;
        } elseif (!$this->validSeconds($seconds)) {
            return ['status' => 'failed',
                    'detail' => sprintf(
                        gettext('A port can be asked to blink for between 1 and %d seconds.'),
                        self::IDENTIFY_SECONDS_MAX
                    )];
        }
        $result = $this->response($this->run('flicker', [$if, $seconds, '']));
        /* Same reason as identifyAction: the script explains itself in English and the page is
           read in Arabic, so the two refusals it can give are said again where gettext() reaches
           them. Which one it was is decided by what the script said, not by guessing here. */
        if (($result['status'] ?? '') === 'error') {
            $message = (string)($result['message'] ?? '');
            if (strpos($message, 'link is down') !== false) {
                $result['detail'] = gettext(
                    'This port has no link, so there is no activity light to beat.'
                );
            } else {
                $result['detail'] = gettext(
                    'Nothing has been seen behind this port to send to, so its activity light '
                    . 'cannot be made to beat. Give the port a neighbour address in the settings.'
                );
            }
        }
        return $result;
    }

    /**
     * Give the LED back early, for the person who has already found the socket and does not want
     * to stand there waiting for a blink they are finished with.
     */
    public function stopIdentifyAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        return $this->response($this->run('stopidentify'));
    }

    /**
     * Take a reading now instead of waiting for the next cycle.
     *
     * The sweep is the same one cron runs and says nothing on success, so there would be no answer
     * to hand back. The document it just wrote is the answer anyone asking for a reading wants, so
     * that is what is returned - and if the sweep declined because the previous one is still
     * younger than the poll interval, this is the reading that declining left in place.
     */
    public function collectNowAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $this->run('collect');
        return $this->response($this->run('status'));
    }

    /**
     * send a short test message to check the mail settings
     */
    public function sendTestMailAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        return $this->response($this->run('testmail'));
    }
}
